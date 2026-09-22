"""Who has been on the server: names, the IPs behind them, when, and what they
said.  The page an admin reads before deciding a ban.  No Qt.

Keyed by name (case-insensitive).  ``by_ip`` answers "who else used this
address" - the cheap rejoin check.  Chat is kept per name, last 200 lines.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

MAX_CHAT_LINES = 200
MAX_PLAYERS = 5000
SESSION_GAP_SECONDS = 120     # away longer than this = a new visit


@dataclass
class PlayerRecord:
    name: str
    ips: dict = field(default_factory=dict)         # ip -> times seen at a poll
    first_seen: float = 0.0
    last_seen: float = 0.0
    visits: int = 0
    chat: deque = field(default_factory=lambda: deque(maxlen=MAX_CHAT_LINES))

    def last_ip(self) -> str:
        if not self.ips:
            return ""
        return max(self.ips.items(), key=lambda kv: kv[1][1])[0]

    def to_json(self) -> dict:
        return {"name": self.name, "ips": self.ips, "first_seen": self.first_seen,
                "last_seen": self.last_seen, "visits": self.visits, "chat": list(self.chat)}

    @classmethod
    def from_json(cls, data: dict) -> "PlayerRecord":
        rec = cls(str(data.get("name", "")))
        for ip, meta in (data.get("ips") or {}).items():
            try:
                rec.ips[str(ip)] = [int(meta[0]), float(meta[1])]
            except (TypeError, ValueError, IndexError):
                continue
        rec.first_seen = float(data.get("first_seen") or 0.0)
        rec.last_seen = float(data.get("last_seen") or 0.0)
        rec.visits = int(data.get("visits") or 0)
        for line in data.get("chat") or []:
            if isinstance(line, list) and len(line) == 2:
                rec.chat.append([float(line[0]), str(line[1])])
        return rec


class PlayerHistory:
    def __init__(self):
        self._players: dict[str, PlayerRecord] = {}
        self.dirty = False

    # -- feeding it ----------------------------------------------------------
    def observe(self, players: Iterable[dict], ips: dict, now: float) -> None:
        """One admin-port poll: players = dicts with 'name'; ips = name -> ip."""
        for player in players:
            name = str(player.get("name", "")).strip()
            if not name or name.lower() == "host":
                continue
            rec = self._players.get(name.lower())
            if rec is None:
                if len(self._players) >= MAX_PLAYERS:
                    oldest = min(self._players, key=lambda k: self._players[k].last_seen)
                    del self._players[oldest]
                rec = PlayerRecord(name, first_seen=now)
                self._players[name.lower()] = rec
            if now - rec.last_seen > SESSION_GAP_SECONDS:
                rec.visits += 1
            rec.name = name
            rec.last_seen = now
            ip = str(ips.get(name, "") or "")
            if ip:
                meta = rec.ips.setdefault(ip, [0, now])
                meta[0] += 1
                meta[1] = now
            self.dirty = True

    def chat(self, name: str, text: str, now: float) -> None:
        rec = self._players.get((name or "").strip().lower())
        if rec is None:
            return
        rec.chat.append([now, text])
        self.dirty = True

    # -- reading it ----------------------------------------------------------
    def get(self, name: str) -> Optional[PlayerRecord]:
        return self._players.get((name or "").strip().lower())

    def all(self) -> list:
        return sorted(self._players.values(), key=lambda r: r.last_seen, reverse=True)

    def by_ip(self, ip: str) -> list:
        """Every name that has used this exact address, most recent first."""
        hits = [r for r in self._players.values() if ip in r.ips]
        return sorted(hits, key=lambda r: r.ips[ip][1], reverse=True)

    def search(self, text: str) -> list:
        t = (text or "").strip().lower()
        if not t:
            return self.all()
        return [r for r in self.all() if t in r.name.lower() or any(t in ip for ip in r.ips)]

    # -- persistence ---------------------------------------------------------
    def to_json(self) -> dict:
        return {"players": [r.to_json() for r in self._players.values()]}

    @classmethod
    def from_json(cls, data) -> "PlayerHistory":
        history = cls()
        if isinstance(data, dict):
            for item in data.get("players") or []:
                if isinstance(item, dict) and item.get("name"):
                    rec = PlayerRecord.from_json(item)
                    history._players[rec.name.lower()] = rec
        return history


def describe_when(stamp: float, now: Optional[float] = None) -> str:
    if not stamp:
        return "-"
    now = time.time() if now is None else now
    ago = max(0, int(now - stamp))
    if ago < 60:
        return "just now"
    if ago < 3600:
        return f"{ago // 60} min ago"
    if ago < 86400:
        return f"{ago // 3600} h ago"
    return time.strftime("%d %b %Y", time.localtime(stamp))
