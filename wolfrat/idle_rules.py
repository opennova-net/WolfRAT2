"""Idle kicker rules, no Qt.  The Babstats feature WolfRAT lacked (2026-09-22).

A player is idle when their position has not changed.  Position comes from
the server process (jo_players.SlotInfo.pos, int32 X/Y/Z at entity+0x04);
Joint Ops itself has no idle handling and kills/deaths are not idleness (a
camping sniper is playing).  Rules:

* off by default; N minutes without moving = kick, with a chat warning
  ``warn_seconds`` before;
* the ban whitelist and (by default) everyone on the Mods list are exempt;
* no position (server not on this PC, map loading, no entity yet) = the
  clock does not run;
* a map change or a rejoin restarts everyone's clock.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Optional

DEFAULT_MINUTES = 10
MIN_MINUTES, MAX_MINUTES = 1, 120
WARN_SECONDS = 60
MOVE_EPSILON = 2000     # engine units; a 20 s run was ~1.5 million, standing still is exactly 0


@dataclass
class IdleConfig:
    enabled: bool = False
    minutes: int = DEFAULT_MINUTES
    exempt_mods: bool = True

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data) -> "IdleConfig":
        cfg = cls()
        if isinstance(data, dict):
            cfg.enabled = bool(data.get("enabled", False))
            try:
                cfg.minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(data.get("minutes", DEFAULT_MINUTES))))
            except (TypeError, ValueError):
                cfg.minutes = DEFAULT_MINUTES
            cfg.exempt_mods = bool(data.get("exempt_mods", True))
        return cfg


@dataclass(frozen=True)
class IdleEvent:
    kind: str          # "warn" | "kick"
    name: str
    player: dict       # the full admin-port record (punting needs all of it)
    idle_seconds: int


def _moved(a, b) -> bool:
    return any(abs(int(x) - int(y)) > MOVE_EPSILON for x, y in zip(a, b))


class IdleWatch:
    def __init__(self):
        self._last_pos: dict[str, tuple] = {}
        self._since: dict[str, float] = {}
        self._warned: set[str] = set()
        self._kicked: dict[str, float] = {}

    def reset(self) -> None:
        """Map change: everyone starts fresh."""
        self._last_pos.clear(); self._since.clear(); self._warned.clear(); self._kicked.clear()

    def idle_seconds(self, name: str, now: float) -> Optional[int]:
        key = name.lower()
        return int(now - self._since[key]) if key in self._since else None

    def tick(self, config: IdleConfig, players: Iterable[dict], positions: dict, exempt: set, now: float) -> list:
        """players = admin-port dicts; positions = name -> (x, y, z) or None;
        exempt = lower-case names never kicked.  Returns IdleEvents."""
        events = []
        present = set()
        limit = config.minutes * 60
        for player in players:
            name = str(player.get("name", ""))
            key = name.lower()
            if not name or key == "host":
                continue
            present.add(key)
            pos = positions.get(name)
            if not pos or all(int(v) == 0 for v in pos):
                # no reading: clock does not run (and does not reset either)
                continue
            last = self._last_pos.get(key)
            if last is None or _moved(last, pos):
                self._last_pos[key] = tuple(pos)
                self._since[key] = now
                self._warned.discard(key)
                continue
            if not config.enabled or key in exempt:
                continue
            idle = now - self._since[key]
            if idle >= limit:
                if now - self._kicked.get(key, -1e9) < 30:
                    continue
                self._kicked[key] = now
                events.append(IdleEvent("kick", name, dict(player), int(idle)))
            elif idle >= limit - WARN_SECONDS and key not in self._warned:
                self._warned.add(key)
                events.append(IdleEvent("warn", name, dict(player), int(idle)))
        for store in (self._last_pos, self._since, self._kicked):
            for key in [k for k in store if k not in present]:
                del store[key]
        self._warned &= present
        return events


def warn_text(name: str, seconds: int = WARN_SECONDS) -> str:
    return f"{name}: move or you will be kicked for idling in {seconds}s"[:62]


def kick_text(name: str, minutes: int) -> str:
    return f"{name} was kicked for being idle {minutes} min"[:62]
