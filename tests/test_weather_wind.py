"""Wind (cloud speed): fades we walk ourselves, handed back to the map's own."""

import random

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make
from wolfrat import weather as w
from wolfrat import weather_dynamic as d
from wolfrat.weather import Addr, Weather, WeatherController


def wind(server):
    return server.peek(Addr.CLOUD_SPEED_TARGET) >> 10


def controller(server, clock):
    control = WeatherController(server, clock=lambda: clock[0])
    control.verify()
    return control


def test_wind_fades_in_over_the_fade_time():
    server, clock = FakeServer(), [0.0]                  # the map's wind is 15
    control = controller(server, clock)
    sky = Weather(cloud_speed=215, fade_seconds=20)
    control.apply(sky)
    assert wind(server) == 15                            # not a jump
    clock[0] = 10
    control.apply(sky)                                   # re-asserting does not restart it
    assert wind(server) == 115
    clock[0] = 20
    control.step_wind()
    assert wind(server) == 215


def test_clear_fades_back_to_the_wind_as_found_then_lets_go():
    server, clock = FakeServer(), [0.0]
    server.poke(Addr.CLOUD_SPEED_TARGET, 50 << 10)       # e.g. the map's script said skyspeed(50)
    control = controller(server, clock)
    control.apply(Weather(precip_percent=60, cloud_speed=200, fade_seconds=0))
    assert wind(server) == 200
    control.apply(w.CLEAR)                               # fade 20 s
    clock[0] = 10
    control.step_wind()
    assert wind(server) == 125
    clock[0] = 25
    control.step_wind()
    assert wind(server) == 50
    server.writes.clear()
    server.poke(Addr.CLOUD_SPEED_TARGET, 80 << 10)       # the map's own again: ours to leave alone
    clock[0] = 40
    control.step_wind()
    control.apply(w.CLEAR)
    assert wind(server) == 80 and Addr.CLOUD_SPEED_TARGET not in server.writes


def test_a_sky_without_wind_puts_the_maps_own_back():
    """Storm (wind 230) then Rain (no wind): the rain does not keep the storm's wind."""
    server, clock = FakeServer(), [0.0]
    control = controller(server, clock)
    control.apply(w.replace(w.PRESETS["storm"], fade_seconds=0))
    assert wind(server) == 230
    control.apply(w.replace(w.PRESETS["rain"], fade_seconds=0))
    assert wind(server) == 15


def test_a_map_change_during_our_wind_keeps_ours_and_learns_the_new_maps():
    server, clock = FakeServer(), [0.0]
    control = controller(server, clock)
    sky = Weather(overcast_percent=50, cloud_speed=180, fade_seconds=0)
    control.apply(sky)
    server.poke(Addr.CLOUD_SPEED_TARGET, 206 << 10)      # map load: the engine snaps to the new map's
    control.apply(sky)
    assert wind(server) == 180                           # ours is put back
    control.apply(w.replace(w.CLEAR, fade_seconds=0))
    assert wind(server) == 206                           # and "clear" = the NEW map's wind


def test_a_map_change_while_going_home_hands_off_at_once():
    server, clock = FakeServer(), [0.0]
    control = controller(server, clock)
    control.apply(Weather(overcast_percent=50, cloud_speed=180, fade_seconds=0))
    control.apply(w.CLEAR)                               # fading home over 20 s
    clock[0] = 5
    control.step_wind()
    server.poke(Addr.CLOUD_SPEED_TARGET, 206 << 10)      # map load mid-fade
    server.writes.clear()
    clock[0] = 10
    control.step_wind()
    assert wind(server) == 206 and server.writes == []


def test_snapshot_remembers_the_maps_wind_not_ours():
    server, clock = FakeServer(), [0.0]
    control = controller(server, clock)
    control.apply(Weather(overcast_percent=50, cloud_speed=180, fade_seconds=0))
    saved = control.snapshot()
    assert saved["cloud"] == 15 << 10
    control.restore(saved, 0)
    assert wind(server) == 15


def test_describe_and_words():
    assert Weather(cloud_speed=150).describe() == "wind 150 (windy)"
    assert w.CLEAR.describe() == "clear"
    assert [w.wind_word(v) for v in (0, 15, 60, 120, 206)] == ["still", "light", "breezy", "windy", "gale"]


# ---- dynamic fronts ----------------------------------------------------------

def test_a_front_rolls_its_wind_builds_it_and_fog_is_still():
    config = d.DynamicConfig(wind_min=100, wind_max=200)
    for seed in range(20):
        front = d.build_front("storm", config, random.Random(seed))
        winds = [stage.sky.cloud_speed for stage in front.stages]
        peak = next(stage for stage in front.stages if stage.peak).sky.cloud_speed
        assert 100 <= peak <= 200 and peak == max(winds)
        assert winds[0] < peak and winds[-1] < peak          # picks up, dies down
        fog = d.build_front("fog", config, random.Random(seed))
        assert fog.stages[0].sky.cloud_speed <= 30


def test_wind_off_leaves_every_maps_own():
    front = d.build_front("storm", d.DynamicConfig(wind=False), random.Random(3))
    assert all(stage.sky.cloud_speed is None for stage in front.stages)


def test_wind_does_not_change_the_rest_of_the_front():
    on = d.build_front("rain", d.DynamicConfig(), random.Random(9))
    off = d.build_front("rain", d.DynamicConfig(wind=False), random.Random(9))
    assert [(s.seconds, s.label) for s in on.stages] == [(s.seconds, s.label) for s in off.stages]
    assert [w.replace(s.sky, cloud_speed=None) for s in on.stages] == [s.sky for s in off.stages]


def test_old_settings_get_wind_on():
    config = d.DynamicConfig.from_json({"enabled": True, "fog_min": 100})
    assert config.wind and (config.wind_min, config.wind_max) == (60, 220)
    assert d.DynamicConfig.from_json(config.to_json()) == config


# ---- the tab -------------------------------------------------------------------

def test_manual_wind_slider(qtbot, tmp_path):
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server)
    assert not tab.wind_slider.isEnabled()
    tab.overcast_slider.setValue(0)
    tab.precip_slider.setValue(0)
    tab.fade_slider.setValue(1)
    tab.wind_cb.setChecked(True)
    tab.wind_slider.setValue(190)
    assert tab.wind_val.text() == "190 (gale)"
    tab.apply_btn.click()
    assert tab._schedule.active.cloud_speed == 190
    assert "wind 190 (gale)" in tab.active_lbl.text()
    import time
    time.sleep(1.1)
    tab._poll()
    assert wind(server) == 190
    assert "wind 190 (gale)" in tab.sky_lbl.text()
    saved = (tmp_path / "wolfrat_weather.json").read_text(encoding="utf-8")
    assert '"wind_on": true' in saved and '"wind": 190' in saved


def test_dynamic_page_wind_controls(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, FakeServer())
    page = tab.dynamic_page
    assert page.wind_cb.isChecked() and "60 (breezy) to 220 (gale)" in page.wind_lbl.text()
    page.wind_min.setValue(10)
    page.wind_max.setValue(90)
    assert (page.config().wind_min, page.config().wind_max) == (10, 90)
    page.wind_cb.setChecked(False)
    assert not page.wind_min.isEnabled() and not page.config().wind
    assert "keeps its own wind" in page.wind_lbl.text()
