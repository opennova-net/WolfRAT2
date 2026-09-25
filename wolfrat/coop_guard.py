"""No team swaps on co-op maps.

On a co-op map the other team is the bots.  Players used ``!switch`` to join
them, kill their own side and switch back.  The admin port never says which
game mode is running, so the mode is read from the server process on the same
PC (jo_players.LocalServerPlayers.read_game_type).  When the server is on
another PC the mode is unknown and swaps are allowed; a co-op-only server
simply turns ``!switch`` off (Dale, 2026-09-23).

Co-op is ``(value & 0xFFFDFFFF) == 0x10020``; a co-op value has not been seen
live yet.

Every swap ends in ServerManager._swap_player_now, which calls ``check()`` -
the safety net.  The chat commands and buttons ask ``blocks_swaps()`` first so
they can answer politely instead of failing.
"""

from __future__ import annotations

from typing import Callable, Optional

COOP_MASK, COOP_VALUE = 0xFFFDFFFF, 0x10020

# The engine's own short names; the long names are ours.
GAME_TYPES = {
    0x00000: "Deathmatch",
    0x00001: "King of the Hill",
    0x00008: "FM",
    0x10000: "Team Deathmatch",
    0x10001: "Team King of the Hill",
    0x10002: "Attack & Defend",
    0x10004: "Capture the Flag",
    0x10008: "Flagball",
    0x10010: "Advance & Secure",
    0x50010: "CAC",
}

REFUSAL = "No team swaps on co-op maps"


class CoopSwapBlocked(RuntimeError):
    """A swap was refused because the server is on a co-op map."""

    def __init__(self) -> None:
        super().__init__(REFUSAL + " (Chat Bot tab > Team Swaps to change this)")


def is_coop(game_type: Optional[int]) -> bool:
    return game_type is not None and (game_type & COOP_MASK) == COOP_VALUE


def game_type_label(game_type: Optional[int]) -> str:
    if game_type is None:
        return "unknown"
    if is_coop(game_type):
        return "Co-op"
    return GAME_TYPES.get(game_type, f"game type {game_type:#x}")


class CoopGuard:
    """``enabled`` = block swaps on co-op (on by default)."""

    def __init__(self, enabled: bool = True,
                 game_type_source: Optional[Callable[[], Optional[int]]] = None) -> None:
        self.enabled = bool(enabled)
        self.game_type_source = game_type_source or (lambda: None)

    def game_type(self) -> Optional[int]:
        try:
            return self.game_type_source()
        except Exception:
            return None

    def is_coop(self) -> bool:
        return is_coop(self.game_type())

    def blocks_swaps(self) -> bool:
        return self.enabled and self.is_coop()

    def check(self, allow_coop: bool = False) -> None:
        if not allow_coop and self.blocks_swaps():
            raise CoopSwapBlocked()
