"""WolfRAT's own team balance (Dale, 2026-09-23): 2+ extra players -> warn
30 s ahead -> volunteers with !1 -> move volunteers first, random after.
Every chat line fits 62 characters."""

import json
import random
import sys

import pytest

from wolfrat import auto_balance as ab
from wolfrat.auto_balance import Balancer, Move, Say

COOP, AAS, DM = 0x10020, 0x10010, 0x00000


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def roster(t1, t2, host=True):
    players = [{"id": "0", "name": "Host", "team": "0"}] if host else []
    players += [{"id": str(10 + i), "name": f"Blue{i}", "team": "1"} for i in range(t1)]
    players += [{"id": str(50 + i), "name": f"Red{i}", "team": "2"} for i in range(t2)]
    return players


def says(actions):
    return [a.text for a in actions if isinstance(a, Say)]


def moves(actions):
    return [a.player["name"] for a in actions if isinstance(a, Move)]


def to_countdown(balancer, clock, players):
    assert balancer.tick(players, True) == []             # gap seen: settling
    clock.now += ab.SETTLE_SECONDS
    return balancer.tick(players, True)                    # warning


# ---- chat lines ------------------------------------------------------------------

def test_every_line_fits_62_without_being_cut():
    for bigger in (1, 2):
        for gap in range(2, 20):
            for line in ab.warning_lines(bigger, gap):
                assert len(line) <= 62
            assert ab.warning_lines(bigger, gap)[1].endswith("to volunteer")
            assert ab.warning_lines(bigger, gap)[0].endswith(("Rebels", "Joint Ops"))
    assert len(ab.CANCEL_LINE) <= 62
    assert len(ab.volunteer_line("X" * 40, 2)) <= 62


def test_moved_lines_split_names_over_62_char_lines():
    names = [f"LongPlayerName{i:02d}" for i in range(9)]
    lines = ab.moved_lines(names, 2)
    assert len(lines) > 1 and all(len(l) <= 62 for l in lines)
    assert all(l.startswith("Moved to Rebels: ") for l in lines)
    joined = ", ".join(l.split(": ", 1)[1] for l in lines)
    assert joined == ", ".join(names)
    assert ab.moved_lines(["Dale"], 1) == ["Moved to Joint Ops: Dale"]


# ---- which games -------------------------------------------------------------------

def test_team_games_only_never_co_op():
    assert ab.is_team_game(AAS, None)
    assert ab.is_team_game(0x10000, None)               # TDM
    assert not ab.is_team_game(COOP, "AS-Anything.bms")   # memory wins over the filename
    assert not ab.is_team_game(DM, "TDM-Looks.bms")
    assert ab.is_team_game(None, "TDM-Spanaird.bms")      # server on another PC: filename
    assert ab.is_team_game(None, "AAS-Doslin.bms")
    assert not ab.is_team_game(None, "DM-Dust.bms")
    assert not ab.is_team_game(None, "COOP_Mission1.bms") # unknown family: leave it alone
    assert not ab.is_team_game(None, None)


# ---- the flow ----------------------------------------------------------------------

def test_warns_after_the_gap_holds_then_moves_a_volunteer():
    clock = Clock()
    b = Balancer(clock=clock, rng=random.Random(1))
    players = roster(6, 4)
    assert says(to_countdown(b, clock, players)) == ab.warning_lines(1, 2)
    assert says(b.volunteer("blue3")) == ["Blue3 volunteered to join Rebels"]
    assert b.volunteer("Blue3") == []                     # once each
    assert b.volunteer("Red1") == []                      # smaller team can't volunteer
    clock.now += ab.COUNTDOWN_SECONDS - 5
    assert b.tick(players, True) == []                    # not yet
    clock.now += 5
    actions = b.tick(players, True)
    assert moves(actions) == ["Blue3"]
    assert says(actions) == ["Moved to Rebels: Blue3"]
    assert [a.to_team for a in actions if isinstance(a, Move)] == [2]


def test_no_volunteers_means_random_players_from_the_bigger_team():
    clock = Clock()
    b = Balancer(clock=clock, rng=random.Random(7))
    players = roster(3, 7)                                # Rebels +4 -> move 2
    to_countdown(b, clock, players)
    clock.now += ab.COUNTDOWN_SECONDS
    moved = moves(b.tick(players, True))
    assert len(moved) == 2 and all(n.startswith("Red") for n in moved)


def test_more_volunteers_than_needed_picks_among_volunteers_only():
    seen = set()
    for seed in range(40):
        clock = Clock()
        b = Balancer(clock=clock, rng=random.Random(seed))
        players = roster(6, 4)
        to_countdown(b, clock, players)
        for n in ("Blue0", "Blue1", "Blue2"):
            b.volunteer(n)
        clock.now += ab.COUNTDOWN_SECONDS
        moved = moves(b.tick(players, True))
        assert len(moved) == 1 and moved[0] in ("Blue0", "Blue1", "Blue2")
        seen.add(moved[0])
    assert len(seen) == 3                                 # really random among them


def test_few_volunteers_then_random_fills_the_rest():
    clock = Clock()
    b = Balancer(clock=clock, rng=random.Random(3))
    players = roster(8, 2)                                # +6 -> move 3
    to_countdown(b, clock, players)
    b.volunteer("Blue5")
    clock.now += ab.COUNTDOWN_SECONDS
    moved = moves(b.tick(players, True))
    assert len(moved) == 3 and "Blue5" in moved


def test_gap_closing_cancels_out_loud_during_countdown_quietly_before():
    clock = Clock()
    b = Balancer(clock=clock)
    assert b.tick(roster(6, 4), True) == []
    assert b.tick(roster(5, 5), True) == [] and b.state == "idle"   # settled itself, nobody told
    to_countdown(b, clock, roster(6, 4))
    assert says(b.tick(roster(5, 4), True)) == [ab.CANCEL_LINE]
    assert b.state == "idle"


def test_one_player_gap_never_triggers_and_host_is_not_counted():
    clock = Clock()
    b = Balancer(clock=clock)
    for _ in range(20):
        clock.now += 10
        assert b.tick(roster(5, 4), True) == []


def test_pausing_mid_countdown_is_silent_and_starts_over():
    clock = Clock()
    b = Balancer(clock=clock)
    to_countdown(b, clock, roster(6, 4))
    b.volunteer("Blue0")
    assert b.tick(roster(6, 4), False) == []              # map vote started / co-op / turned off
    assert b.state == "idle" and b.volunteers == {}


def test_cooldown_after_a_balance():
    clock = Clock()
    b = Balancer(clock=clock, rng=random.Random(2))
    players = roster(6, 4)
    to_countdown(b, clock, players)
    clock.now += ab.COUNTDOWN_SECONDS
    assert moves(b.tick(players, True))
    for _ in range(int(ab.COOLDOWN_SECONDS / 5) - 1):
        clock.now += 5
        assert b.tick(players, True) == []                # swaps still landing: stay quiet
    clock.now += 10
    b.tick(players, True)
    assert b.state == "settling"


def test_switching_freely_is_allowed_it_just_counts():
    # Dale: never refuse !switch for balance - a stacked team simply gets balanced later.
    clock = Clock()
    b = Balancer(clock=clock)
    assert b.tick(roster(7, 3), True) == [] and b.state == "settling"


# ---- the panel ----------------------------------------------------------------------

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat.auto_balance_panel import SETTINGS_FILE, AutoBalancePanel  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class Rig:
    def __init__(self, folder, game_type=AAS, vote=False):
        self.said, self.moved, self.jo = [], [], []
        self.clock = Clock()
        self.game_type, self.vote = game_type, vote
        self.folder = str(folder)
        self.panel = self.build()

    def build(self):
        panel = AutoBalancePanel(self.folder, say=self.said.append,
                                 move=lambda p, team: self.moved.append((p["name"], team)),
                                 set_jo_balance=self.jo.append, clock=self.clock, rng=random.Random(5))
        panel.game_type_source = lambda: self.game_type
        panel.current_map_source = lambda: "AAS-Doslin.bms"
        panel.vote_active_source = lambda: self.vote
        return panel

    def choose(self, mode):
        self.panel.mode_combo.setCurrentIndex(self.panel.mode_combo.findData(mode))

    def poll(self, players, seconds=5):
        self.clock.now += seconds
        self.panel.on_players(players)


def test_panel_is_off_by_default_and_remembers_the_choice(tmp_path):
    rig = Rig(tmp_path)
    assert rig.panel.mode == ab.MODE_OFF
    rig.choose(ab.MODE_WOLFRAT)
    rig.panel.gap_spin.setValue(3)
    saved = json.loads((tmp_path / SETTINGS_FILE).read_text())
    assert saved == {"mode": "wolfrat", "gap": 3}
    again = Rig(tmp_path)
    assert again.panel.mode == ab.MODE_WOLFRAT and again.panel.balancer.gap == 3


def test_choosing_joint_ops_turns_the_server_setting_on_and_leaving_turns_it_off(tmp_path):
    rig = Rig(tmp_path)
    rig.choose(ab.MODE_WOLFRAT)
    assert rig.jo == []                                   # Off -> WolfRAT: server untouched
    rig.choose(ab.MODE_JOINTOPS)
    rig.choose(ab.MODE_WOLFRAT)
    rig.choose(ab.MODE_JOINTOPS)
    rig.choose(ab.MODE_OFF)
    assert rig.jo == [True, False, True, False]
    rig.panel.on_settings({"AutoBalanceOnRecycle": "1"})
    assert "ON on the server" in rig.panel.status_text()


def test_panel_runs_the_whole_thing_from_polls_and_chat(tmp_path):
    rig = Rig(tmp_path)
    rig.choose(ab.MODE_WOLFRAT)
    rig.panel.on_chat([{"id": 1, "text": "Blue2: !1"}])  # history from before we connected
    players = roster(6, 4)
    rig.poll(players)
    rig.poll(players, ab.SETTLE_SECONDS)
    assert rig.said == ab.warning_lines(1, 2)
    assert "Balancing in 30s - 0 volunteers" in rig.panel.status_text()
    rig.panel.on_chat([{"id": 1, "text": "Blue2: !1"}, {"id": 2, "text": "Blue4: !1"},
                       {"id": 3, "text": "Red0: !1"}, {"id": 4, "text": "Blue4: hello !1"}])
    assert rig.said[-1] == "Blue4 volunteered to join Rebels"   # old line ignored, Red0 ignored
    rig.poll(players, ab.COUNTDOWN_SECONDS)
    assert rig.moved == [("Blue4", 2)]
    assert rig.said[-1] == "Moved to Rebels: Blue4"


def test_panel_pauses_for_co_op_other_modes_votes_and_when_off(tmp_path):
    for game_type, vote, mode, words in ((COOP, False, ab.MODE_WOLFRAT, "co-op"),
                                         (DM, False, ab.MODE_WOLFRAT, "not a team game"),
                                         (AAS, True, ab.MODE_WOLFRAT, "map vote"),
                                         (AAS, False, ab.MODE_OFF, "off"),
                                         (AAS, False, ab.MODE_JOINTOPS, "each new map starts")):
        rig = Rig(tmp_path / f"{game_type}{vote}{mode}", game_type=game_type, vote=vote)
        (tmp_path / f"{game_type}{vote}{mode}").mkdir(exist_ok=True)
        rig.choose(mode)
        for _ in range(20):
            rig.poll(roster(8, 2), 10)
        assert rig.said == [] and rig.moved == [], (game_type, vote, mode)
        assert words in rig.panel.status_text()
