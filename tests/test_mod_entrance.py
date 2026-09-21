"""Moderator entrance: who gets a fanfare, when, and how often."""
import random

from wolfrat.mod_entrance import (CHAT_MAX_LEN, DEFAULT_LINES, EntranceConfig,
                                  ModEntrance, line_problem, render_line)

MODS = ["BadgerLove", "Oscarmike247"]


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


def make(**cfg):
    clock = Clock()
    config = EntranceConfig(enabled=True, **cfg)
    return ModEntrance(config, clock=clock, rng=random.Random(1)), clock


def run(engine, clock, names, seconds, mods=MODS, step=5):
    """Refresh the list every `step` seconds, collect everything announced."""
    out = []
    for _ in range(0, seconds, step):
        clock.tick(step)
        out += engine.update(names, mods)
    return out


def test_mod_joining_gets_one_entrance_after_the_delay():
    engine, clock = make()
    engine.update(["Rookie"], MODS)
    assert run(engine, clock, ["Rookie", "BadgerLove"], 20) == []
    due = run(engine, clock, ["Rookie", "BadgerLove"], 15)
    assert [e.player for e in due] == ["BadgerLove"]
    assert "BadgerLove" in due[0].message and due[0].lightning
    assert run(engine, clock, ["Rookie", "BadgerLove"], 600) == []


def test_ordinary_player_gets_nothing():
    engine, clock = make()
    engine.update([], MODS)
    assert run(engine, clock, ["Rookie"], 120) == []


def test_off_by_default():
    clock = Clock()
    engine = ModEntrance(clock=clock)
    engine.update([], MODS)
    assert run(engine, clock, ["BadgerLove"], 120) == []


def test_people_already_playing_when_wolfrat_starts_are_not_announced():
    engine, clock = make()
    assert run(engine, clock, ["BadgerLove", "Rookie"], 120) == []


def test_rejoin_inside_the_cooldown_is_silent_and_after_it_is_announced():
    engine, clock = make(cooldown_minutes=30)
    engine.update([], MODS)
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1
    for _ in range(5):                       # shaky connection
        run(engine, clock, [], 20)
        assert run(engine, clock, ["BadgerLove"], 60) == []
    run(engine, clock, [], 31 * 60)
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1


def test_cooldown_survives_a_restart():
    engine, clock = make()
    engine.update([], MODS)
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1
    saved = dict(engine.last_announced)
    fresh = ModEntrance(EntranceConfig(enabled=True), last_announced=saved,
                        clock=clock, rng=random.Random(2))
    fresh.update([], MODS)
    assert run(fresh, clock, ["BadgerLove"], 60) == []
    assert fresh.minutes_until_ready("badgerlove") > 0


def test_map_change_is_not_a_join_even_when_players_trickle_back():
    engine, clock = make(cooldown_minutes=0)     # the cooldown must not be what saves us
    engine.update([], MODS)
    assert len(run(engine, clock, ["BadgerLove", "Oscarmike247", "Rookie"], 60)) == 2
    engine.note_disconnected()
    assert run(engine, clock, [], 45) == []
    assert run(engine, clock, ["Rookie"], 10) == []
    assert run(engine, clock, ["Rookie", "BadgerLove"], 10) == []
    assert run(engine, clock, ["Rookie", "BadgerLove", "Oscarmike247"], 120) == []


def test_mod_who_joins_just_before_a_map_change_is_announced_after_it():
    engine, clock = make()
    engine.update(["Rookie"], MODS)
    assert run(engine, clock, ["Rookie", "BadgerLove"], 10) == []
    engine.note_disconnected()
    assert run(engine, clock, [], 60) == []
    assert run(engine, clock, ["Rookie", "BadgerLove"], 20) == []   # loading in again
    assert len(run(engine, clock, ["Rookie", "BadgerLove"], 15)) == 1


def test_mod_who_leaves_before_the_delay_gets_nothing_and_keeps_the_cooldown():
    engine, clock = make()
    engine.update([], MODS)
    assert run(engine, clock, ["BadgerLove"], 10) == []
    assert run(engine, clock, [], 120) == []
    assert engine.minutes_until_ready("BadgerLove") == 0
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1


def test_a_real_leave_and_return_without_a_connection_drop_is_a_join():
    engine, clock = make(cooldown_minutes=0)
    engine.update([], MODS)
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1
    run(engine, clock, [], 60)
    assert len(run(engine, clock, ["BadgerLove"], 60)) == 1


def test_removed_from_the_mods_list_while_waiting():
    engine, clock = make()
    engine.update([], MODS)
    run(engine, clock, ["BadgerLove"], 10)
    assert run(engine, clock, ["BadgerLove"], 60, mods=["Oscarmike247"]) == []


def test_lightning_switch_and_name_case():
    engine, clock = make(lightning=False)
    engine.update([], MODS)
    due = run(engine, clock, ["badgerLOVE"], 60)
    assert len(due) == 1 and not due[0].lightning and "badgerLOVE" in due[0].message


def test_lines_always_fit_the_chat_box_and_never_clip_the_name():
    for name in ("Bo", "BadgerLove", "A" * 16, "B" * 31):
        for line in DEFAULT_LINES + ("x" * 60 + " {player}",):
            text = render_line(line, name)
            assert len(text) <= CHAT_MAX_LEN and name in text


def test_line_warnings_are_plain():
    assert line_problem("All rise for {player}.") is None
    assert "nobody will know" in line_problem("A mod has joined")
    assert "Too long" in line_problem("x" * 55 + "{player}")


def test_same_line_is_not_used_twice_running():
    engine, clock = make(cooldown_minutes=0)
    engine.update([], MODS)
    seen = []
    for _ in range(12):
        seen += [e.message for e in run(engine, clock, ["BadgerLove"], 60)]
        run(engine, clock, [], 30)
    assert len(seen) == 12 and all(a != b for a, b in zip(seen, seen[1:]))


def test_config_round_trip_and_bad_values():
    cfg = EntranceConfig.from_dict({"enabled": 1, "cooldown_minutes": "abc",
                                    "delay_seconds": 9999, "lines": ["", "  "]})
    assert cfg.enabled and cfg.cooldown_minutes == 30 and cfg.delay_seconds == 300
    assert cfg.lines == list(DEFAULT_LINES)
    assert EntranceConfig.from_dict(cfg.to_dict()).to_dict() == cfg.to_dict()
    assert EntranceConfig.from_dict(None).enabled is False
