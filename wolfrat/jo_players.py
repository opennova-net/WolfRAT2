"""Each player's IP, read from the Joint Ops server process on this PC.

The admin port never sends an IP, so this is the only way WolfRAT can see one.
Proven live 2026-09-22 (spike ``wolfcontrol/spike_jo_ip_read.py``): the read
needs no elevation when WolfRAT runs as the same Windows user as the server.

Chain (Jointops.exe.kong.c, Server_PlayerPuntCRCMisMatch @ 0x50F380):
    capacity  = dword [0x24C0CA4]
    slotPtr   = dword [0x24C0CA8]          one slot = 0x188E8 bytes
    slot+4    = active byte
    slot+40   = name, 32 bytes
    slot+28   = CNetPlayer*  ->  +196 = IPv4, network byte order
    slot+0    = GamePlayerEntity*  ->  +0x04/+0x08/+0x0C = int32 X/Y/Z position
                (Entity_ValidatePtr @ 0x500910; proven 2026-09-22 with Dale walking:
                 ~400k units/s moving, exactly constant standing still)
Slot 0 is the host itself (null CNetPlayer).  Read-only: this module never
opens the process for writing.

AAS zones (GameEvent_FlagCapture @ 0x50F6F0, ZoneSlotChain_* helpers; proven
2026-09-22 with Dale capturing Charlie then Bravo on Doslin Oblast):
    g_zone_slot_chain @ 0x24D1EBC: dword +44 = vector begin, +48 = vector end
    element = ZoneEntry*  ->  +0 = PSP entity*
    PSP entity +354 = owning team byte (0 / 1 Joint Ops / 2 Rebels), +538 = tier,
               +4 = int32 X/Y/Z like a player
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional

from wolfrat import weather

CAPACITY_VA = 0x24C0CA4
SLOTPTR_VA = 0x24C0CA8
SLOT_STRIDE = 0x188E8
NAME_OFFSET, NAME_LEN = 40, 32
NETPLAYER_OFFSET = 28
IP_OFFSET = 196
ENTITY_OFFSET = 0
POSITION_OFFSET = 4          # three int32: X, Y, Z(height)
MAX_CAPACITY = 256
RETRY_SECONDS = 10
ZONE_CHAIN_VA = 0x24D1EBC
ZONE_TEAM_OFFSET, ZONE_TIER_OFFSET = 354, 538
MAX_ZONES = 64


@dataclass(frozen=True)
class SlotInfo:
    slot: int
    name: str
    ip: str
    pos: Optional[tuple] = None      # (x, y, z) engine units, None when unreadable


@dataclass(frozen=True)
class ZoneInfo:
    tier: int
    team: int
    pos: Optional[tuple] = None


class LocalServerPlayers:
    """``read()`` -> list of SlotInfo, or [] with ``status`` saying why."""

    def __init__(self, clock=time.time, find=None, open_memory=None):
        self._clock = clock
        self._find = find or weather.find_process_ids
        self._open = open_memory or (lambda pid: weather.ProcessMemory(pid, writable=False))
        self._memory = None
        self._pid: Optional[int] = None
        self._next_try = 0.0
        self.status = "Not looked yet."
        self.available = False

    def _attach(self) -> bool:
        now = self._clock()
        if self._memory is not None:
            return True
        if now < self._next_try:
            return False
        self._next_try = now + RETRY_SECONDS
        if sys.platform != "win32":
            self.status = "IP reading only works on Windows."
            return False
        try:
            pids = self._find()
        except Exception as exc:                 # pragma: no cover - OS oddities
            self.status = f"Could not list processes: {exc}"
            return False
        if not pids:
            self.status = ("jointops.exe is not running on this PC - IPs need the server "
                           "on the same machine as WolfRAT.")
            return False
        try:
            self._memory = self._open(pids[0])
            self._pid = pids[0]
        except Exception as exc:
            self.status = str(exc)
            self._memory = None
            return False
        return True

    def _drop(self, why: str) -> None:
        try:
            if self._memory is not None:
                self._memory.close()
        except Exception:
            pass
        self._memory, self._pid = None, None
        self.status = why
        self.available = False

    def exe_path(self) -> str:
        if self._memory is None:
            return ""
        try:
            return self._memory.exe_path()
        except Exception:
            return ""

    def read(self) -> list:
        if not self._attach():
            self.available = False
            return []
        mem = self._memory
        try:
            capacity = struct.unpack("<I", mem.read(CAPACITY_VA, 4))[0]
            base = struct.unpack("<I", mem.read(SLOTPTR_VA, 4))[0]
        except Exception as exc:
            self._drop(f"Lost the server process ({exc}).")
            return []
        if not base or capacity == 0 or capacity > MAX_CAPACITY:
            self._drop("The server process does not look like Joint Ops (slot table not found).")
            return []
        found = []
        for index in range(capacity):
            slot = base + index * SLOT_STRIDE
            try:
                head = mem.read(slot, NAME_OFFSET + NAME_LEN)
            except Exception:
                continue
            if not head[4]:
                continue
            name = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0].decode("latin-1", "replace")
            netplayer = struct.unpack_from("<I", head, NETPLAYER_OFFSET)[0]
            ip = ""
            if netplayer:
                try:
                    raw = mem.read(netplayer + IP_OFFSET, 4)
                    if any(raw):
                        ip = socket.inet_ntoa(raw)
                except Exception:
                    ip = ""
            entity = struct.unpack_from("<I", head, ENTITY_OFFSET)[0]
            pos = None
            if entity:
                try:
                    pos = struct.unpack("<iii", mem.read(entity + POSITION_OFFSET, 12))
                except Exception:
                    pos = None
            found.append(SlotInfo(index, name, ip, pos))
        self.available = True
        self.status = f"Reading IPs from jointops.exe (pid {self._pid})."
        return found


    def read_zones(self) -> list:
        """Every AAS zone on the current map, or [] (non-AAS map or no process)."""
        if not self._attach():
            return []
        mem = self._memory
        try:
            begin = struct.unpack("<I", mem.read(ZONE_CHAIN_VA + 44, 4))[0]
            end = struct.unpack("<I", mem.read(ZONE_CHAIN_VA + 48, 4))[0]
        except Exception:
            return []
        if not begin or end < begin or (end - begin) > 4 * MAX_ZONES:
            return []
        zones = []
        for address in range(begin, end, 4):
            try:
                entry = struct.unpack("<I", mem.read(address, 4))[0]
                entity = struct.unpack("<I", mem.read(entry, 4))[0] if entry else 0
                if not entity:
                    continue
                team = mem.read(entity + ZONE_TEAM_OFFSET, 1)[0]
                tier = mem.read(entity + ZONE_TIER_OFFSET, 1)[0]
                pos = struct.unpack("<iii", mem.read(entity + POSITION_OFFSET, 12))
            except Exception:
                continue
            zones.append(ZoneInfo(tier, team, pos))
        return zones


def ips_by_name(slots) -> dict:
    """name -> ip for every slot that has both (the host has neither)."""
    return {s.name: s.ip for s in slots if s.name and s.ip}


def positions_by_name(slots) -> dict:
    """name -> (x, y, z) for every slot with a readable entity."""
    return {s.name: s.pos for s in slots if s.name and s.pos}
