"""CTF / Flagball early vote (Dale, 2026-09-23): start the vote when a team is
N flags / goals from winning - done the same way as the Deathmatch / TDM kill
watch, so the normal "minutes before the end" rule still starts it if nobody
gets that close.  Captures are read from the server on this PC."""

import struct
import sys
import tempfile

import pytest

from wolfrat import vote_rules as vr
from wolfrat.jo_players import (CAPS_OFFSET, CTF_GOAL_VA, GAME_CTF, GAME_FB, MAX_SCORE_VA,
                                TEAM_SCORE_VA, LocalServerPlayers, caps_to_go)


# ---- what "to go" means -----------------------------------------------------------

def test_ctf_to_go_is_each_teams_enemy_flags_minus_its_captures():
    assert caps_to_go(GAME_CTF, {1: 0, 2: 0}, {1: 3, 2: 3}, 5) == 3
    assert caps_to_go(GAME_CTF, {1: 2, 2: 1}, {1: 3, 2: 3}, 5) == 1
    assert caps_to_go(GAME_CTF, {1: 0, 2: 1}, {1: 2, 2: 4}, 5) == 2      # closest team counts
    assert caps_to_go(GAME_CTF, {1: 5, 2: 0}, {1: 3, 2: 3}, 5) == 0
    assert caps_to_go(GAME_CTF, {1: 0, 2: 0}, {1: 0, 2: 0}, 5) is None   # no flags counted yet


def test_flagball_to_go_is_max_score_minus_the_leader():
    assert caps_to_go(GAME_FB, {1: 3, 2: 1}, {1: 0, 2: 0}, 5) == 2
    assert caps_to_go(GAME_FB, {1: 3, 2: 1}, {1: 0, 2: 0}, 65000) is None  # no limit set
    assert caps_to_go(GAME_FB, {1: 3, 2: 1}, {1: 0, 2: 0}, 0) is None
    assert caps_to_go(0x10010, {1: 3, 2: 1}, {1: 0, 2: 0}, 5) is None      # not CTF/FB


class FakeMemory:
    def __init__(self, dwords):
        self.dwords = dwords

    def read(self, address, size):
        return struct.pack("<i", self.dwords[address])

    def close(self):
        pass


def test_reader_reads_the_score_blocks_and_goals(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    mem = FakeMemory({TEAM_SCORE_VA[1] + CAPS_OFFSET: 2, TEAM_SCORE_VA[2] + CAPS_OFFSET: 0,
                      CTF_GOAL_VA[1]: 3, CTF_GOAL_VA[2]: 3, MAX_SCORE_VA: 5})
    reader = LocalServerPlayers(find=lambda: [1], open_memory=lambda pid: mem)
    assert reader.read_caps_to_go(GAME_CTF) == 1
    assert reader.read_caps_to_go(GAME_FB) == 3
    assert reader.read_caps_to_go(0x10010) is None


# ---- the rule ------------------------------------------------------------------------

def view(mode, remaining, caps, total=30):
    return vr.MatchView(f"{mode}-Map.bms", remaining, total, mode=mode, caps_to_go=caps)


def rules(mode, on=True, within=1):
    out = vr.rules_from_json(None)
    out[mode] = vr.ModeRule(caps_watch_enabled=on, caps_before_win=within)
    return out


@pytest.mark.parametrize("mode,word", [(vr.MODE_CTF, "flag"), (vr.MODE_FB, "goal")])
def test_fires_when_a_team_gets_within_n(mode, word):
    watch = vr.KillWatch()
    d = vr.decide(view(mode, 20, 3), 3, rules(mode), vr.KillWatch(), watch)
    assert not d.fire and f"when a team is 1 {word} from winning" in d.status
    d = vr.decide(view(mode, 18, 1), 3, rules(mode), vr.KillWatch(), watch)
    assert d.fire and f"a team is 1 {word} from winning" in d.reason


def test_plural_words():
    assert vr.caps_words(vr.MODE_CTF, 1) == "1 flag" and vr.caps_words(vr.MODE_CTF, 2) == "2 flags"
    assert vr.caps_words(vr.MODE_FB, 1) == "1 goal" and vr.caps_words(vr.MODE_FB, 3) == "3 goals"


def test_time_rule_still_wins_when_nobody_gets_close():
    watch = vr.KillWatch()
    for remaining in range(30, 3, -1):
        assert not vr.decide(view(vr.MODE_CTF, remaining, 3), 3, rules(vr.MODE_CTF), vr.KillWatch(), watch).fire
    d = vr.decide(view(vr.MODE_CTF, 3, 3), 3, rules(vr.MODE_CTF), vr.KillWatch(), watch)
    assert d.fire and "3m remaining" in d.reason


def test_stale_scores_at_map_start_do_not_fire():
    # Last map ended 0 to go; a poll can still show that on the new map.
    watch = vr.KillWatch()
    assert not vr.decide(view(vr.MODE_FB, 29, 0), 3, rules(vr.MODE_FB), vr.KillWatch(), watch).fire
    assert not vr.decide(view(vr.MODE_FB, 28, 5), 3, rules(vr.MODE_FB), vr.KillWatch(), watch).fire
    assert vr.decide(view(vr.MODE_FB, 20, 1), 3, rules(vr.MODE_FB), vr.KillWatch(), watch).fire


def test_off_by_default_and_ignored_without_the_server():
    watch = vr.KillWatch()
    assert not vr.decide(view(vr.MODE_CTF, 20, 3), 3, vr.rules_from_json(None), vr.KillWatch(), watch).fire
    assert not vr.decide(view(vr.MODE_CTF, 19, 0), 3, vr.rules_from_json(None), vr.KillWatch(), watch).fire
    none_read = vr.decide(view(vr.MODE_CTF, 19, None), 3, rules(vr.MODE_CTF), vr.KillWatch(), watch)
    assert not none_read.fire and "flags" not in none_read.status


def test_settings_round_trip_and_old_files_load_off():
    saved = vr.rules_to_json(rules(vr.MODE_FB, within=2))
    assert vr.rules_from_json(saved)[vr.MODE_FB] == vr.ModeRule(caps_watch_enabled=True, caps_before_win=2)
    old = {"fb": {"minutes_in_enabled": True, "minutes_in": 12}}
    assert vr.rules_from_json(old)[vr.MODE_FB].caps_watch_enabled is False


def test_server_game_type_beats_the_filename():
    assert vr.family_from_game_type(GAME_CTF) == vr.MODE_CTF
    assert vr.family_from_game_type(GAME_FB) == vr.MODE_FB
    assert vr.family_from_game_type(0x10010) == vr.MODE_AS
    assert vr.family_from_game_type(0) is None          # left to the filename
    assert vr.family_from_game_type(None) is None


# ---- the tab -----------------------------------------------------------------------

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

import wolfrat.app as app_module  # noqa: E402
from wolfrat.admin_commands import GameSettings  # noqa: E402
from wolfrat.runtime import DesktopRuntime  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class Server:
    def __init__(self):
        self.players = [{"id": 1}, {"id": 2}]
        self.player_entries = ()
        self.game_settings = GameSettings({"KillLimit": "100"})

    def set_raw_chat_callback(self, cb):
        pass


class Missions:
    _rotation_maps = ["A.bms", "B.bms"]

    def _find_display_name(self, f):
        return f

    def _mission_entry_at(self, i):
        return None


@pytest.fixture
def tab(monkeypatch):
    monkeypatch.setattr(app_module, "wire_log", lambda *_: None)
    t = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(tempfile.mkdtemp()))
    t._tick_timer.stop()
    t.fired = []
    t._start_vote = lambda: (t.fired.append(t._server_game_time_remaining), setattr(t, "_vote_stage", "done"))
    t.enable_cb.setChecked(True)
    t.trigger_spin.setValue(3)
    return t


def test_only_ctf_and_flagball_get_the_row(tab):
    assert set(tab._caps_widgets) == {vr.MODE_CTF, vr.MODE_FB}
    assert not tab._caps_widgets[vr.MODE_CTF][0].isChecked()


def test_ctf_map_with_a_plain_filename_uses_the_servers_mode(tab):
    # "Barrens.bms" guesses as Other modes; the server says CTF.
    tab.game_type_now = lambda: GAME_CTF
    state = {"to_go": 3}
    tab.caps_to_go = lambda: state["to_go"]
    cb, spin = tab._caps_widgets[vr.MODE_CTF]
    cb.setChecked(True)
    tab.on_missions_updated(["3: Barrens.bms - () () () <CURRENT MISSION> <>"])
    tab.update_game_time(30, 25)
    tab._tick()
    assert tab.fired == [] and "Capture the Flag" in tab.current_mode_lbl.text()
    state["to_go"] = 1
    tab.update_game_time(30, 22)
    tab._tick()
    assert tab.fired == [22]


def test_saved_and_reloaded(tab):
    cb, spin = tab._caps_widgets[vr.MODE_FB]
    cb.setChecked(True)
    spin.setValue(2)
    folder = tab._recently_played_file.rsplit("wolfrat_recently_played.json", 1)[0]
    again = app_module.MapVotingTab(Server(), Missions(), DesktopRuntime.production(folder))
    again._tick_timer.stop()
    cb2, spin2 = again._caps_widgets[vr.MODE_FB]
    assert cb2.isChecked() and spin2.value() == 2
