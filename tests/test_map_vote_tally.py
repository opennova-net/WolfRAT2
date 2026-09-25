"""One player, one vote - however the vote reaches the Map Voting tab.

Up to v2.8.4 every vote counted twice: the tab's raw-chat listener recorded
``FMJ-BadgerLove`` while the Mods tab forwarded the same line as
``fmj-badgerlove`` (it lower-cases senders).  Two keys, one player, and the
"already voted" guard never fired.  Seen live 2026-09-25: two voters,
"Votes: 1:2, 2:2 (4 total)".

The fix: ``on_vote`` is the only writer and keys on the lower-cased name.
"""

import sys
import tempfile

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication

import wolfrat.app as app_module
from wolfrat.admin_commands import GameSettings
from wolfrat.runtime import DesktopRuntime

_app = QApplication.instance() or QApplication(sys.argv[:1])


class Server:
    def __init__(self):
        self.players = [{"id": 1, "name": "FMJ-BadgerLove"}, {"id": 2, "name": "Belsman"}]
        self.player_entries = ()
        self.game_settings = GameSettings({})

    def set_raw_chat_callback(self, cb):
        pass


class Missions:
    _rotation_maps = ["TK-One.bms", "AS-Two.npj", "FB-Three.npj", "DM-Four.bms", "CTF-Five.bms"]

    def _find_display_name(self, f):
        return f

    def _mission_entry_at(self, i):
        return None


@pytest.fixture
def tab(monkeypatch):
    monkeypatch.setattr(app_module, "wire_log", lambda *_: None)
    tab = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(tempfile.mkdtemp()))
    tab._tick_timer.stop()
    tab._vote_active = True
    tab.choices_spin.setValue(5)
    tab._map_choices = [(None, f) for f in Missions._rotation_maps]
    assert len(tab._map_choices) == tab.choices_spin.value()
    tab._votes = {}
    return tab


def feed_every_path(tab, name, opt):
    """The same vote arriving by all three routes WolfRAT has ever used."""
    tab._on_raw_chat(f"{name}: !{opt}\r\n")                     # raw chat listener, name as typed
    tab.on_chat([{"name": name, "text": f"!{opt}"}])            # parsed chat
    tab.on_vote(name.lower(), opt)                              # what the Mods tab used to forward


def test_same_player_three_routes_is_one_vote(tab):
    feed_every_path(tab, "FMJ-BadgerLove", 2)
    feed_every_path(tab, "Belsman", 1)
    counts, winner, total = tab._tally_votes()
    assert total == 2
    assert (counts[1], counts[2]) == (1, 1)


def test_case_of_the_name_never_splits_a_voter(tab):
    tab.on_vote("Belsman", 1)
    tab.on_vote("BELSMAN", 3)
    tab.on_vote(" belsman ", 2)
    assert tab._votes == {"belsman": 1}


def test_options_four_and_five_count_like_the_others(tab):
    # The old Mods-tab forward only covered !1..!3, so on a 5-choice vote
    # !4/!5 counted once while !1..!3 counted twice - enough to flip a winner.
    feed_every_path(tab, "A", 1)
    feed_every_path(tab, "B", 4)
    feed_every_path(tab, "C", 5)
    counts, _, total = tab._tally_votes()
    assert total == 3 and counts[1] == counts[4] == counts[5] == 1


def test_server_lines_and_bad_options_are_ignored(tab):
    tab._on_raw_chat("Server: !1\n")
    tab.on_vote("server", 1)
    tab.on_vote("Belsman", 6)
    tab.on_vote("Belsman", 0)
    tab.on_vote("", 1)
    tab.on_vote("Belsman", "x")
    assert tab._votes == {}


def test_no_votes_written_when_no_vote_is_running(tab):
    tab._vote_active = False
    feed_every_path(tab, "Belsman", 1)
    assert tab._votes == {}


def test_mods_tab_no_longer_forwards_votes():
    import ast
    import inspect
    src = inspect.getsource(app_module)
    tree = ast.parse(src)
    forwards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "on_vote"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id != "self"
    ]
    assert forwards == [], "only MapVotingTab itself may call on_vote"
