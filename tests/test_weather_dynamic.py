"""Dynamic weather, replayed over thousands of simulated hours."""

import random
from collections import Counter

import pytest

from tests.test_weather import FakeServer
from wolfrat import weather as w
from wolfrat import weather_dynamic as d
from wolfrat.weather_dynamic import DynamicConfig, DynamicWeather


def run(config, hours, seed=1, step=5, **context):
    now = [0.0]
    engine = DynamicWeather(clock=lambda: now[0], rng=random.Random(seed))
    ctx = dict(map_name="TD-Adale.bms", players=8, in_game=True, manual_active=False)
    ctx.update(context)
    out = []
    while now[0] < hours * 3600:
        out.append((now[0], engine.tick(config, **ctx), engine.front))
        now[0] += step
    return out


def on(**kw):
    return DynamicConfig(enabled=True, **kw)


def only(*kinds):
    return {name: (1 if name in kinds else 0) for name in d.TYPES}


# ---- names ------------------------------------------------------------------

@pytest.mark.parametrize("typed, real", [
    ("cod for kill house", "TD-COD4_KillHouse.bms"),
    ("COD4 KillHouse", "TD-COD4_KillHouse.bms"),
    ("killhouse", "TD-COD4_KillHouse.bms"),
    ("TD-COD4_KillHouse.bms", "TD-COD4_KillHouse.bms"),
    ("doslin oblast", "AS-DoslinOblast.bms"),
    ("Doslin Oblsat", "AS-DoslinOblast.bms"),
    ("black rock", "AS - Black Rock TAC.npj"),
])
def test_typed_names_find_the_real_map(typed, real):
    assert d.map_rule_for(real, {typed: d.MAP_NONE}) == d.MAP_NONE


@pytest.mark.parametrize("typed, real", [
    ("kill house", "AS-DoslinOblast.bms"), ("rock", "TD-Adale.bms"), ("", "TD-Adale.bms"),
    ("as", "AS-DoslinOblast.bms"),
])
def test_names_do_not_match_the_wrong_map(typed, real):
    assert d.map_rule_for(real, {typed: d.MAP_NONE}) == d.MAP_NORMAL


def test_exact_beats_contained():
    rules = {"Spanaird": d.MAP_NONE, "TDM-Spanaird Night": d.MAP_SNOW}
    assert d.map_rule_for("TDM-Spanaird Night.bms", rules) == d.MAP_SNOW
    assert d.map_rule_for("AAS-Spanaird.bms", rules) == d.MAP_NONE


# ---- the long run -----------------------------------------------------------

def test_mix_follows_the_weights_and_only_ticked_types_appear():
    config = on(frequency="frequent",
                weights={"overcast": 0, "drizzle": 10, "rain": 30, "storm": 60,
                         "fog": 0, "snow": 0, "blizzard": 0})
    kinds = Counter()
    seen = None
    for _, _, front in run(config, hours=600, step=20):
        if front is not None and front is not seen:
            kinds[front.kind] += 1
        seen = front
    total = sum(kinds.values())
    assert total > 300 and set(kinds) == {"drizzle", "rain", "storm"}
    assert 0.52 < kinds["storm"] / total < 0.68
    assert 0.23 < kinds["rain"] / total < 0.37


@pytest.mark.parametrize("frequency", list(d.FREQUENCIES))
def test_clear_spells_and_peaks_stay_inside_the_chosen_spans(frequency):
    config = on(frequency=frequency)
    clear_min, clear_max, hold_min, hold_max = config.spans()
    log = run(config, hours=300, step=10)
    clear_runs, run_start, fronts = [], 0.0, []
    previous = None
    for t, _, front in log:
        if front is not None and previous is None:
            clear_runs.append(t - run_start)
        if front is None and previous is not None:
            run_start = t
        if front is not None and front is not previous:
            fronts.append(front)
        previous = front
    assert len(fronts) > 20
    for gap in clear_runs[1:]:
        assert clear_min * 60 - 10 <= gap <= clear_max * 60 + 20
    for front in fronts:
        peak = [s for s in front.stages if s.peak]
        assert len(peak) == 1 and hold_min * 60 <= peak[0].seconds <= hold_max * 60
        assert 0.55 <= front.strength <= 1.0


def test_a_front_climbs_and_comes_back_down_the_same_ladder():
    front = d.build_front("storm", on(), random.Random(3))
    assert [s.label for s in front.stages] == ["Overcast", "Rain", "Storm", "Rain", "Overcast"]
    amounts = [s.sky.precip_percent for s in front.stages]
    assert amounts[0] == 0 and amounts[2] == max(amounts) and amounts[1] == amounts[3]
    assert [s.sky.fog_metres is not None for s in front.stages] == [False, False, True, False, False]


def test_fog_is_rolled_inside_the_chosen_range_and_varies():
    config = on(fog_min=90, fog_max=260)
    rolled = {d.build_front("fog", config, random.Random(i)).stages[0].sky.fog_metres for i in range(200)}
    assert min(rolled) >= 90 and max(rolled) <= 260 and len(rolled) > 40


def test_nothing_ticked_means_clear_forever():
    config = on(frequency="constant", weights=only())
    assert all(front is None for _, _, front in run(config, hours=5))


def test_off_means_the_server_is_left_alone():
    assert all(dec.sky is None for _, dec, _ in run(DynamicConfig(enabled=False), hours=1))


# ---- pausing: the clock keeps running, the writing stops --------------------

@pytest.mark.parametrize("context", [
    {"manual_active": True}, {"in_game": False}, {"players": 0},
])
def test_pauses_write_nothing(context):
    assert all(dec.sky is None for _, dec, _ in run(on(frequency="constant"), hours=3, **context))


def test_empty_server_can_still_have_weather_if_wanted():
    log = run(on(frequency="constant", pause_when_empty=False), hours=2, players=0)
    assert any(dec.sky is not None and dec.sky != w.CLEAR for _, dec, _ in log)


def test_a_front_carries_across_a_map_change():
    now = [0.0]
    engine = DynamicWeather(clock=lambda: now[0], rng=random.Random(5))
    config = on(frequency="constant")
    ctx = dict(players=4, manual_active=False)
    while engine.front is None:
        engine.tick(config, map_name="A.bms", in_game=True, **ctx)
        now[0] += 5
    front = engine.front
    for _ in range(12):                                  # a minute of loading
        assert engine.tick(config, map_name="A.bms", in_game=False, **ctx).sky is None
        now[0] += 5
    decision = engine.tick(config, map_name="B.bms", in_game=True, **ctx)
    assert engine.front is front and decision.sky is not None and decision.sky != w.CLEAR
    assert "until clear" in decision.status


# ---- per-map rules ----------------------------------------------------------

def test_no_weather_map_gets_clear_sky_and_no_chat():
    config = on(frequency="constant", map_rules={"kill house": d.MAP_NONE},
                quake_enabled=True, quake_every=5)
    log = run(config, hours=6, map_name="TD-COD4_KillHouse.bms")
    assert all(dec.sky.describe() == "clear" and not dec.announce and not dec.quake_seconds
               for _, dec, _ in log)


def test_snow_instead_and_never_snow():
    rainy = on(frequency="constant", weights=only("storm"), map_rules={"arctic": d.MAP_SNOW})
    log = run(rainy, hours=4, map_name="AS-ArcticBase.bms")
    skies = [dec.sky for _, dec, _ in log if dec.sky]
    assert any(s.precip_percent > 0 for s in skies)
    assert all(s.snow for s in skies if s.precip_percent > 0)
    assert any("blizzard" in dec.announce for _, dec, _ in log)

    snowy = on(frequency="constant", weights=only("blizzard"), map_rules={"desert": d.MAP_NO_SNOW})
    skies = [dec.sky for _, dec, _ in run(snowy, hours=4, map_name="TD-DesertStorm.bms") if dec.sky]
    assert any(s.precip_percent > 0 for s in skies) and not any(s.snow for s in skies)


def test_snow_front_waits_for_the_rain_to_stop():
    now = [0.0]
    engine = DynamicWeather(clock=lambda: now[0], rng=random.Random(2))
    config = on(frequency="constant", weights=only("snow"))
    ctx = dict(map_name="x", players=3, in_game=True, manual_active=False)
    for _ in range(200):
        decision = engine.tick(config, sky_is_dry=False, sky_is_snow=False, **ctx)
        now[0] += 5
    assert engine.front is None and decision.sky == w.CLEAR and "dry" in decision.status
    engine.tick(config, sky_is_dry=True, sky_is_snow=False, **ctx)
    assert engine.front is not None


# ---- extras -----------------------------------------------------------------

def test_quakes_are_rare_short_and_off_by_default():
    assert not any(dec.quake_seconds for _, dec, _ in run(on(), hours=100, step=20))
    config = on(quake_enabled=True, quake_every=90, quake_max_seconds=6)
    quakes = [dec.quake_seconds for _, dec, _ in run(config, hours=600, step=20) if dec.quake_seconds]
    assert 250 < len(quakes) < 550 and all(2 <= q <= 6 for q in quakes)


def test_lightning_only_at_the_peak_of_a_storm():
    flashes = 0
    for _, dec, front in run(on(frequency="constant"), hours=60):
        if dec.lightning:
            flashes += 1
            assert front.kind == "storm"
    assert flashes > 20
    assert not any(dec.lightning for _, dec, _ in run(on(frequency="constant", lightning=False), hours=20))


def test_announcements_are_few_and_can_be_silenced():
    log = run(on(frequency="frequent"), hours=50)
    said = [dec.announce for _, dec, _ in log if dec.announce]
    fronts = len({id(f) for _, _, f in log if f})
    assert said and len(said) <= 3 * fronts + 1
    assert not any(dec.announce for _, dec, _ in run(on(frequency="frequent", announce=False), hours=20))


def test_config_survives_a_round_trip_and_rubbish():
    config = on(frequency="custom", clear_min=3, clear_max=9,
                map_rules={"kill house": d.MAP_NONE, "x": "bogus"})
    again = DynamicConfig.from_json(config.to_json())
    assert again.spans() == (3, 9, 5, 12) and again.map_rules == {"kill house": d.MAP_NONE}
    assert DynamicConfig.from_json("nonsense").enabled is False
    assert DynamicConfig.from_json({"frequency": "hourly", "weights": {"rain": "lots"}}).frequency == "normal"


# ---- end to end on the engine-accurate fake ---------------------------------

def test_whole_fronts_on_the_fake_server():
    now = [0.0]
    server = FakeServer()
    control = w.WeatherController(server)
    engine = DynamicWeather(clock=lambda: now[0], rng=random.Random(11))
    config = on(frequency="constant", weights=only("storm"))
    peak_seen, fog_targets, fronts, last = 0, [], 0, None
    for _ in range(int(3 * 3600 / 5)):
        decision = engine.tick(config, map_name="m", players=5, in_game=True, manual_active=False,
                               sky_is_dry=server.peek(w.Addr.PRECIP_CURRENT) < 1300)
        if decision.sky is not None:
            control.apply(decision.sky)
        if engine.front is not None and engine.front is not last:
            fronts += 1
        last = engine.front
        server.tick(5)
        now[0] += 5
        fog_targets.append(server.peek(w.Addr.FOG_TARGET) >> 16)
        peak_seen = max(peak_seen, server.client_sees()["precip"])
    assert fronts >= 5 and peak_seen > 130               # real storms got through
    changes = sum(1 for a, b in zip(fog_targets, fog_targets[1:]) if a != b)
    assert changes <= 2 * fronts                         # fog moves twice per front, no more


def test_reasserting_every_five_seconds_does_not_stretch_the_fade():
    server = FakeServer()
    control = w.WeatherController(server)
    sky = w.Weather(precip_percent=100, fade_seconds=120)
    for _ in range(26):                                  # 130 s of 5 s re-asserts
        control.apply(sky)
        server.tick(5)
    assert server.client_sees()["precip"] >= 250
