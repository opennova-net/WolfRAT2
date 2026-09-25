"""Wildlife: make the TAC mod's sharks hunt.

The TAC mod (OscarMike247's Revx02 pack) places sharks as team 0 with a 100 m
attack distance.  Team 0 means they have nobody to target, and 100 m makes
them lunge from far away and miss.  Two numbers in the running server fix
that: team 3 (hostile to everyone) and an attack distance of about 10 m.
Both revert when a map loads and when a shark respawns, so this keeps
re-applying them for as long as the switch is on.

Everything here works through the same ``Memory`` the Weather tab holds
(read/write of the live server on this PC).  Addresses are bare constants
by design; where they came from is in the private vault.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

SHARK_NAME = b"Shark\0"
HOSTILE_TEAM = 3            # hostile to everyone; the map spawns them as 0
DEFAULT_ATTACK_M = 10       # tested: circles, charges, bites.  25 misses; 6 too close
MIN_ATTACK_M = 4
MAX_ATTACK_M = 25
MAP_DEFAULT_ATTACK_M = 100


class Addr:
    ENTITY_BASE = 0xA892E0          # entity table: base, stride, used (3 dwords)
    ENTITY_STRIDE = 0xA892E4
    ENTITY_USED = 0xA892E8


ENT_NAME_PTR = 0x20      # -> item definition name, NUL-terminated
ENT_AI_PTR = 0x68        # -> AI slot (null for non-AI entities)
ENT_HP = 0x11E           # int16
ENT_TEAM = 0x162         # byte
AI_ATTACK_DIST = 0x3C    # 16.16 metres

_MAX_ENTITIES = 8192     # sanity: a real table is a few hundred
_MIN_STRIDE, _MAX_STRIDE = 0x180, 0x10000


class WildlifeError(Exception):
    pass


@dataclass
class Shark:
    address: int
    ai_slot: int
    team: int
    attack_m: float
    hp: int

    @property
    def hunting(self) -> bool:
        return self.team == HOSTILE_TEAM


@dataclass
class Status:
    text: str
    hooked: bool = False          # the green-dot state
    sharks: list = field(default_factory=list)
    reapplied: int = 0


def _u32(mem, address: int) -> int:
    return struct.unpack("<I", mem.read(address, 4))[0]


def scan(mem) -> list[Shark]:
    """Every live shark in the server.  Raises WildlifeError if the entity
    table cannot be read or does not look like one."""
    try:
        base = _u32(mem, Addr.ENTITY_BASE)
        stride = _u32(mem, Addr.ENTITY_STRIDE)
        used = _u32(mem, Addr.ENTITY_USED)
    except Exception as exc:
        raise WildlifeError("Could not read the server's entity list.") from exc
    if not base or not (_MIN_STRIDE <= stride <= _MAX_STRIDE) or used > _MAX_ENTITIES:
        raise WildlifeError("The server's entity list does not look right (different exe?).")
    sharks = []
    for i in range(used):
        ent = base + i * stride
        try:
            name_ptr = _u32(mem, ent + ENT_NAME_PTR)
            if not name_ptr or mem.read(name_ptr, len(SHARK_NAME)) != SHARK_NAME:
                continue
            ai = _u32(mem, ent + ENT_AI_PTR)
            if not ai:
                continue
            team = mem.read(ent + ENT_TEAM, 1)[0]
            attack = _u32(mem, ai + AI_ATTACK_DIST) / 65536
            hp = struct.unpack("<h", mem.read(ent + ENT_HP, 2))[0]
        except Exception:
            continue                      # a half-built slot; next poll sees it
        sharks.append(Shark(ent, ai, team, attack, hp))
    return sharks


def hunt(mem, sharks: list[Shark], attack_m: float, team: int = HOSTILE_TEAM) -> int:
    """Write team + attack distance to every shark that has drifted from them.
    Returns how many sharks were changed.  Raises WildlifeError on a failed write."""
    changed = 0
    packed = struct.pack("<I", int(attack_m * 65536))
    for shark in sharks:
        needs = shark.team != team or abs(shark.attack_m - attack_m) > 0.01
        if not needs:
            continue
        try:
            mem.write(shark.address + ENT_TEAM, bytes([team]))
            mem.write(shark.ai_slot + AI_ATTACK_DIST, packed)
        except Exception as exc:
            raise WildlifeError("Could not write to the server (run WolfRAT as administrator?).") from exc
        shark.team, shark.attack_m = team, attack_m
        changed += 1
    return changed


class SharkKeeper:
    """Polled from the Weather tab.  Keeps sharks hunting while ``enabled``."""

    def __init__(self):
        self.reapplied = 0        # writes since the switch went on
        self._map = None

    def reset(self):
        self.reapplied = 0

    def tick(self, mem, *, enabled: bool, attack_m: float, writable: bool,
             map_name: Optional[str], linked: bool) -> Status:
        where = f" on {map_name}" if map_name else " on this map"
        if not linked:
            return Status("Not linked to a game server on this PC - see the Server tab.")
        if map_name != self._map:
            self._map = map_name
        try:
            sharks = scan(mem)
        except WildlifeError as exc:
            return Status(str(exc))
        if not sharks:
            return Status("No sharks" + where + ". Sharks only exist on TAC mod maps that place them.")
        if not enabled:
            return Status(f"{len(sharks)} shark{'s' if len(sharks) != 1 else ''}{where} - "
                          "hunting is off (they only bite if you touch them).", sharks=sharks)
        if not writable:
            return Status("Sharks found but WolfRAT cannot write to the server "
                          "(run WolfRAT as administrator?).", sharks=sharks)
        try:
            changed = hunt(mem, sharks, attack_m)
        except WildlifeError as exc:
            return Status(str(exc), sharks=sharks)
        self.reapplied += changed
        n = len(sharks)
        text = (f"🟢 {n} shark{'s' if n != 1 else ''} hooked{where} - hunting "
                f"(hostile, {attack_m:g} m).")
        if self.reapplied:
            text += f" Re-applied {self.reapplied}x after map loads/respawns."
        return Status(text, hooked=True, sharks=sharks, reapplied=self.reapplied)
