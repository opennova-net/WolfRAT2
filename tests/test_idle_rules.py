"""Idle kicker rules (2026-09-22)."""
from wolfrat import idle_rules as ir
from wolfrat.idle_rules import IdleConfig, IdleWatch

NOW = 1_800_000_000.0
P = [{"id": "1", "name": "Camper", "team": "1"}, {"id": "2", "name": "Runner", "team": "2"}]


def run(watch, cfg, seconds, pos, exempt=set(), players=P):
    return watch.tick(cfg, players, pos, exempt, NOW + seconds)


def test_off_by_default_and_the_clock_still_runs_for_the_column():
    cfg, watch = IdleConfig(), IdleWatch()
    assert not cfg.enabled
    still = {"Camper": (100, 100, 5), "Runner": (100, 100, 5)}
    assert run(watch, cfg, 0, still) == []
    assert run(watch, cfg, 3600, still) == []
    assert watch.idle_seconds("camper", NOW + 3600) == 3600


def test_warn_then_kick_then_cooldown_and_moving_resets():
    cfg, watch = IdleConfig(enabled=True, minutes=10), IdleWatch()
    still = {"Camper": (100, 100, 5), "Runner": (100, 100, 5)}
    run(watch, cfg, 0, still)
    assert run(watch, cfg, 539, still) == []
    warn = run(watch, cfg, 541, {"Camper": (100, 100, 5), "Runner": (900000, 100, 5)})
    assert [(e.kind, e.name, e.idle_seconds) for e in warn] == [("warn", "Camper", 541)]
    assert warn[0].player == P[0]
    assert run(watch, cfg, 560, {"Camper": (100, 100, 5), "Runner": (900000, 100, 5)}) == []   # warned once
    kick = run(watch, cfg, 600, {"Camper": (100, 100, 5), "Runner": (1800000, 100, 5)})
    assert [(e.kind, e.name) for e in kick] == [("kick", "Camper")]
    assert run(watch, cfg, 610, still) == []                                # 30 s cooldown while the punt lands
    # Runner never idled: moved every time (back to the start counts as a move)
    assert watch.idle_seconds("runner", NOW + 610) == 0


def test_tiny_jitter_is_not_movement_but_a_real_step_is():
    cfg, watch = IdleConfig(enabled=True, minutes=1), IdleWatch()
    run(watch, cfg, 0, {"Camper": (100, 100, 5)}, players=P[:1])
    run(watch, cfg, 30, {"Camper": (100 + ir.MOVE_EPSILON - 1, 100, 5)}, players=P[:1])
    assert watch.idle_seconds("camper", NOW + 30) == 30
    run(watch, cfg, 31, {"Camper": (100 + ir.MOVE_EPSILON + 1, 100, 5)}, players=P[:1])
    assert watch.idle_seconds("camper", NOW + 31) == 0


def test_exempt_names_are_never_warned_or_kicked_but_still_timed():
    cfg, watch = IdleConfig(enabled=True, minutes=1), IdleWatch()
    still = {"Camper": (1, 1, 1)}
    run(watch, cfg, 0, still, exempt={"camper"}, players=P[:1])
    assert run(watch, cfg, 120, still, exempt={"camper"}, players=P[:1]) == []
    assert watch.idle_seconds("Camper", NOW + 120) == 120


def test_no_position_means_no_clock_and_a_map_change_resets():
    cfg, watch = IdleConfig(enabled=True, minutes=2), IdleWatch()
    run(watch, cfg, 0, {"Camper": (1, 1, 1)}, players=P[:1])
    assert run(watch, cfg, 120, {}, players=P[:1]) == []                      # server not on this PC: nothing
    assert run(watch, cfg, 121, {"Camper": (0, 0, 0)}, players=P[:1]) == []   # loading: nothing
    assert watch.idle_seconds("camper", NOW + 121) == 121
    watch.reset()
    assert watch.idle_seconds("camper", NOW + 121) is None
    run(watch, cfg, 122, {"Camper": (1, 1, 1)}, players=P[:1])
    assert run(watch, cfg, 150, {"Camper": (1, 1, 1)}, players=P[:1]) == []   # only 28 s since the reset (warn is at 60)


def test_leaving_forgets_the_player_so_a_rejoin_starts_fresh():
    cfg, watch = IdleConfig(enabled=True, minutes=1), IdleWatch()
    run(watch, cfg, 0, {"Camper": (1, 1, 1)}, players=P[:1])
    run(watch, cfg, 50, {}, players=[])
    run(watch, cfg, 55, {"Camper": (1, 1, 1)}, players=P[:1])
    assert watch.idle_seconds("camper", NOW + 55) == 0


def test_config_round_trip_and_clamp():
    cfg = IdleConfig(enabled=True, minutes=15, exempt_mods=False)
    assert IdleConfig.from_json(cfg.to_json()) == cfg
    assert IdleConfig.from_json({"minutes": 999}).minutes == ir.MAX_MINUTES
    assert IdleConfig.from_json({"minutes": "x"}).minutes == ir.DEFAULT_MINUTES
    assert IdleConfig.from_json("junk") == IdleConfig()


def test_chat_lines_fit_the_62_char_limit():
    assert len(ir.warn_text("A" * 32)) <= 62 and len(ir.kick_text("A" * 32, 120)) <= 62
    assert ir.warn_text("Camper") == "Camper: move or you will be kicked for idling in 60s"
    assert ir.kick_text("Camper", 10) == "Camper was kicked for being idle 10 min"
