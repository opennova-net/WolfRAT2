"""'Link WolfRAT to the server' - what the Server tab's link box says.

Weather, lightning, player IPs, idle kick and zone announcements all need
WolfRAT on the same PC as jointops.exe, and lightning also needs the small
server.wac script.  The Weather tab owns the one connection to the server
process; this module only turns what it knows into the checklist a first-time
admin reads, so the two tabs can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OK, WAIT, NO, OFF = "ok", "wait", "no", "off"

INTRO = ("WolfRAT can do more when it runs on the same PC as your game server: weather and "
         "lightning, player IPs for bans, idle kick and Advance and Secure zone "
         "announcements. Link it once and it looks after itself from then on.")


@dataclass(frozen=True)
class LinkState:
    looked: bool = False               # has WolfRAT looked for the server yet?
    found: bool = False                # a jointops.exe is running on this PC
    reachable: bool = False            # ... and it is a server build WolfRAT can use
    problem: str = ""                  # why not, in the admin's words
    pid: Optional[int] = None
    folder: str = ""
    script_installed: bool = False     # our block is in the folder's server.wac
    writable: bool = False             # WolfRAT holds a handle that can talk to the script
    script_running: Optional[bool] = None   # None = not asked yet
    connected: bool = False            # admin connection is up (the check waits for it)


@dataclass(frozen=True)
class Row:
    mark: str        # OK / WAIT / NO / OFF
    title: str
    detail: str


@dataclass(frozen=True)
class View:
    rows: tuple
    mode: str        # "linked" | "link" | "blocked"
    hint: str


def describe(state: LinkState) -> View:
    if not state.looked:
        return View((Row(WAIT, "Looking for the game server...", ""),
                     Row(OFF, "WolfRAT can reach it", ""),
                     Row(OFF, "Server script", "")), "blocked", "")

    if not state.found:
        return View(
            (Row(NO, "Game server not found on this PC",
                 "No jointops.exe is running here."),
             Row(OFF, "WolfRAT can reach it", ""),
             Row(OFF, "Server script", "")),
            "blocked",
            "Start your game server on this PC and WolfRAT finds it by itself within a few "
            "seconds. If your server runs on another computer, these extras are not "
            "available - everything else in WolfRAT works as normal.")

    where = f"jointops.exe  ·  process {state.pid}" if state.pid else "jointops.exe"
    found = Row(OK, "Server found", where + (f"\n{state.folder}" if state.folder else ""))

    if not state.reachable:
        return View(
            (Row(OK, "Server found", "jointops.exe is running on this PC."),
             Row(NO, "WolfRAT cannot use it", state.problem),
             Row(OFF, "Server script", "")),
            "blocked", "")                    # the row above already says why

    reach = Row(OK, "WolfRAT can reach it",
                "Weather, player IPs, idle kick and zone announcements.")

    if not state.script_installed:
        return View(
            (found, reach, Row(OFF, "Server script", "Not installed yet.")),
            "link",
            "One click: WolfRAT checks the server and adds its small script to server.wac "
            "in your server folder (anything already in that file is kept). "
            "Players download nothing.")

    if state.script_running is True:
        script = Row(OK, "Server script running", "Lightning and thunder are ready.")
    elif state.script_running is False:
        script = Row(OK, "Server script installed",
                     "This map loaded before it went in - it switches on at the next map "
                     "change. Nothing else to do.")
    elif not state.connected:
        script = Row(OK, "Server script installed",
                     "WolfRAT checks it is running once you are connected above.")
    else:
        script = Row(OK, "Server script installed", "Checking it is running...")
    return View((found, reach, script), "linked", "")
