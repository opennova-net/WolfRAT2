"""Private replies: "unknown command" and "not allowed for your rank" go to
the one player who typed it, not the whole server (Dale, 2026-09-23).

Joint Ops has no whisper command, but its script language does: inside
``PLOOP ... END`` (once per player) ``ptext("...")`` sends a chat line to that
player only.  The text is fixed when the script compiles, so the lines live in
server.wac; WolfRAT only pulls the trigger.

The trigger is the game's own per-player script flags, ``pisvar(i)`` /
``psetvar(i)`` (i <= 16; psetvar can only set 1).  WolfRAT sets the "say" flag
on the player's slot; the script whispers and sets the "done" flag; WolfRAT
sees "done" and clears both.  The flags are wiped with the whole slot on
every map change, so nothing lingers.

G249 is this block's "are you there?" slot (WolfRAT writes 3, the block
answers 4), the same handshake the lightning add-on uses on G250.  Until the
block answers on the current map - it is compiled when a map loads - and
whenever the server is not on this PC, the caller falls back to public chat.

Addresses and how each was found: WolfRAT vault, Systems/Memory Map.md.
"""

from __future__ import annotations

import re
import struct
import time
from typing import Callable, Optional

from wolfrat import server_wac

BEGIN = "// >>> WolfRAT whispers (written by WolfRAT - change it in WolfRAT, not here)"
END = "// <<< WolfRAT whispers"
_SECTION_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"[^\n]*\n?", re.DOTALL)

UNKNOWN, DENIED = "unknown", "denied"
# kind -> (say flag, done flag, colour, text).  Flags 13-16: the top of the
# game's 0-16 range, clear of the low numbers map scripts use.
MESSAGES = {
    UNKNOWN: (13, 14, "ff8000", "That command does not exist on this server."),
    DENIED: (15, 16, "ff4040", "Your rank is not allowed to use that command."),
}

PING_VAR = 0x00C6BA40 + 4 * 249          # G249
PING, PONG = 3, 4
CAPACITY_VA, SLOTPTR_VA, SLOT_STRIDE = 0x24C0CA4, 0x24C0CA8, 0x188E8
NAME_OFFSET, NAME_LEN, FLAGS_OFFSET = 40, 32, 0x188
PROBE_SECONDS = 3.0
GIVE_UP_SECONDS = 20.0                   # dead in the spawn screen: the loop skips them


def build_section() -> str:
    lines = [BEGIN,
             "// Replies only the player who typed the command sees.",
             "// G249: 3 = are you there? -> 4",
             "if eq(G249, 3) then",
             "set(G249, 4)",
             "endif",
             "PLOOP"]
    for say, done, colour, text in MESSAGES.values():
        lines += [f"if pisvar({say}) and not pisvar({done}) then",
                  f'ptext("<c{colour}>{text}")',
                  f"psetvar({done})",
                  "endif"]
    return "\n".join(lines + ["END", END]) + "\n"


def install(server_dir) -> str:
    """Put (or refresh) our block in server.wac: "added", "updated" or "unchanged".
    Everything else in the file is kept."""
    section = build_section()
    body = server_wac._read(server_dir)
    if _SECTION_RE.search(body):
        new = _SECTION_RE.sub(lambda _m: section, body, count=1)
        result = "updated"
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        new = body + ("\n" if body else "") + section
        result = "added"
    if new == body:
        return "unchanged"
    server_wac._write(server_dir, new)
    return result


def installed(server_dir) -> bool:
    try:
        return bool(_SECTION_RE.search(server_wac._read(server_dir)))
    except server_wac.ServerWacError:
        return False


class Whisperer:
    """``memory()`` returns a writable handle to the server, or None."""

    def __init__(self, memory: Callable[[], object], clock: Callable[[], float] = time.monotonic):
        self._memory = memory
        self._clock = clock
        self._answer: Optional[bool] = None       # None = not asked yet on this handle
        self._ping_pending = False
        self._next_probe = 0.0
        self._pending: list = []                  # (slot address, say, done, give up at)

    # ---- plumbing ------------------------------------------------------------
    def _dword(self, mem, address: int) -> int:
        return struct.unpack("<i", mem.read(address, 4))[0]

    def available(self) -> bool:
        return self._answer is True

    def _slot_of(self, mem, name: str) -> Optional[int]:
        capacity = self._dword(mem, CAPACITY_VA)
        base = self._dword(mem, SLOTPTR_VA)
        if not base or not 0 < capacity <= 256:
            return None
        wanted = name.strip().lower()
        for index in range(capacity):
            slot = base + index * SLOT_STRIDE
            head = mem.read(slot, NAME_OFFSET + NAME_LEN)
            if not head[4]:
                continue
            slot_name = head[NAME_OFFSET:NAME_OFFSET + NAME_LEN].split(b"\0", 1)[0]
            if slot_name.decode("latin-1", "replace").strip().lower() == wanted:
                return slot
        return None

    # ---- the public side ---------------------------------------------------------
    def whisper_to(self, name: str, kind: str) -> bool:
        """Whisper ``kind`` to ``name``.  False = use public chat instead."""
        if kind not in MESSAGES or not self.available():
            return False
        mem = self._memory()
        if mem is None:
            return False
        say, done, _colour, _text = MESSAGES[kind]
        try:
            slot = self._slot_of(mem, name)
            if slot is None:
                return False
            mem.write(slot + FLAGS_OFFSET + done, b"\x00")
            mem.write(slot + FLAGS_OFFSET + say, b"\x01")
        except Exception:
            return False
        self._pending.append((slot, say, done, self._clock() + GIVE_UP_SECONDS))
        return True

    def tick(self) -> None:
        """Call often (every ~0.25 s): clears flags the script has answered and
        asks the block whether it is loaded on this map."""
        mem = self._memory()
        if mem is None:
            self._answer, self._ping_pending, self._pending = None, False, []
            return
        now = self._clock()
        keep = []
        for slot, say, done, give_up in self._pending:
            try:
                answered = mem.read(slot + FLAGS_OFFSET + done, 1)[0] != 0
                if answered or now >= give_up:
                    mem.write(slot + FLAGS_OFFSET + say, b"\x00")
                    mem.write(slot + FLAGS_OFFSET + done, b"\x00")
                    continue
            except Exception:
                continue
            keep.append((slot, say, done, give_up))
        self._pending = keep
        if now < self._next_probe:
            return
        self._next_probe = now + PROBE_SECONDS
        try:
            value = self._dword(mem, PING_VAR)
            if self._ping_pending:
                if value == PONG:
                    self._answer = True
                elif value == PING:
                    self._answer = False          # nobody picked it up: not loaded on this map
                self._ping_pending = False
            if value in (0, PING, PONG):
                mem.write(PING_VAR, struct.pack("<i", PING))
                self._ping_pending = True
        except Exception:
            self._answer, self._ping_pending = None, False
