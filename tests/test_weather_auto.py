"""Auto: WolfRAT reads the loaded map and picks rain or snow by itself."""

import random

import pytest

from tests.test_weather import FakeServer
from tests.test_weather_dynamic import on, only, run
from tests.test_weather_dynamic_tab import make, only_storms
from wolfrat import weather as w
from wolfrat import weather_dynamic as d


# ---- reading the map -------------------------------------------------------

def test_map_info_reads_the_header_the_server_keeps():
    server = FakeServer()
    server.set_map("TD - Frozen Lake", "DFS2.TRN", climate=0, weather_type=0)
    info = w.WeatherController(server).map_info()
    assert (info.title, info.terrain, info.climate) == ("TD - Frozen Lake", "dfs2", 0)
    assert info.environment == "full_03.env"
    assert "terrain dfs2" in info.describe() and "desert" in info.describe()


@pytest.mark.parametrize("terrain, climate, weather_type, snow", [
    ("dfs1.trn", 0, 0, True),       # snow terrain left on Desert - the case Oscar warned about
    ("DFM15", 1, 0, True),
    ("flatsnow.trn", 0, 0, True),
    ("dvxg1.trn", 2, 0, True),      # mapper said Snow: believe it
    ("dvxg1.trn", 0, 2, True),
    ("dvxg1.trn", 0, 0, False),
    ("dfd2.trn", 1, 1, False),
    ("", 0, 0, False),
])
def test_snow_verdict(terrain, climate, weather_type, snow):
    info = w.MapInfo(terrain=d.terrain_key(terrain), climate=climate, weather_type=weather_type)
    assert d.is_snow_map(info.terrain, info.says_snow, d.DEFAULT_SNOW_TERRAINS) is snow


def test_the_snow_list_is_the_admins_to_edit():
    assert d.is_snow_map("mysnow", False, ["MySnow.trn"]) is True
    assert d.is_snow_map("dfs1", False, []) is False
    config = d.DynamicConfig.from_json({"snow_terrains": ["DFX55.trn", " ", "dfs1"]})
    assert config.snow_terrains == ["dfx55", "dfs1"]
    assert d.DynamicConfig.from_json({}).snow_terrains == list(d.DEFAULT_SNOW_TERRAINS)


# ---- what Auto does to a front ---------------------------------------------

def precip_skies(log):
    return [dec.sky for _, dec, _ in log if dec.sky is not None and dec.sky.precip_percent > 0]


def test_auto_gives_a_snow_map_snow_and_everything_else_rain():
    config = on(frequency="constant", weights=only("storm"))
    snowy = precip_skies(run(config, hours=4, map_is_snow=True))
    assert snowy and all(sky.snow for sky in snowy)
    assert any("blizzard" in dec.announce for _, dec, _ in run(config, hours=4, map_is_snow=True))

    config = on(frequency="constant", weights=only("blizzard"))
    dry_land = precip_skies(run(config, hours=4, map_is_snow=False))
    assert dry_land and not any(sky.snow for sky in dry_land)


def test_unknown_map_leaves_the_front_as_rolled():
    config = on(frequency="constant", weights=only("blizzard"))
    skies = precip_skies(run(config, hours=4, map_is_snow=None))
    assert skies and all(sky.snow for sky in skies)


def test_a_rule_set_by_hand_beats_auto():
    config = on(frequency="constant", weights=only("storm"), map_rules={"adale": d.MAP_NO_SNOW})
    skies = precip_skies(run(config, hours=4, map_is_snow=True))
    assert skies and not any(sky.snow for sky in skies)

    config = on(frequency="constant", weights=only("storm"), map_rules={"adale": d.MAP_SNOW})
    skies = precip_skies(run(config, hours=4, map_is_snow=False))
    assert skies and all(sky.snow for sky in skies)

    config = on(frequency="constant", weights=only("storm"), map_rules={"adale": d.MAP_NONE})
    assert not precip_skies(run(config, hours=4, map_is_snow=True))


def test_old_saved_normal_rules_become_auto():
    config = d.DynamicConfig.from_json({"map_rules": {"a": "normal", "b": "none", "c": "auto"}})
    assert config.map_rules == {"b": d.MAP_NONE}


# ---- through the real tab --------------------------------------------------

def test_the_tab_snows_on_a_snow_terrain_and_says_why(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer()
    server.set_map("TD - Frozen Lake", "dfs2.trn", climate=0)       # snow terrain, Desert climate
    tab, said, _ = make(qtbot, tmp_path, server)
    tab._dynamic._clock = lambda: clock[0]
    tab.current_map = "TD-FrozenLake.bms"
    page = tab.dynamic_page
    only_storms(page)
    page.enabled_cb.setChecked(True)
    for _ in range(int(30 * 60 / 5)):
        clock[0] += 5
        server.tick(5)
        tab._poll()
        if server.client_sees()["precip"] > 100:
            break
    assert server.client_sees()["snow"] == 1
    assert "Auto picked snow" in page.this_map_lbl.text() and "dfs2" in page.this_map_lbl.text()

    # the admin takes over: rain on this map
    page._map_rules["TD-FrozenLake.bms"] = d.MAP_NO_SNOW
    tab._poll()
    assert "set by hand: rain" in page.this_map_lbl.text()
    assert "Auto would pick snow" in page.this_map_lbl.text()


def test_the_map_list_remembers_what_each_map_was(qtbot, tmp_path):
    import json
    server = FakeServer()
    server.set_map("Kill House", "dfs1.trn")
    tab, _, _ = make(qtbot, tmp_path, server)
    tab.current_map = "TD-COD4_KillHouse.bms"
    tab._poll()
    page = tab.dynamic_page
    row = next(r for r in range(page.map_table.rowCount())
               if "KillHouse" in page.map_table.item(r, 0).text())
    assert page.map_table.item(row, 1).text() == "dfs1, snow"
    assert page.map_table.item(row, 2).text() == "Auto"
    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())
    assert saved["seen_maps"] == {"cod4killhouse": "dfs1, snow"}


def test_buttons_are_auto_rain_snow_no_weather(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, FakeServer())
    assert list(d.MAP_RULE_LABELS.values()) == ["Auto", "Rain", "Snow", "No weather"]
    page = tab.dynamic_page
    page.map_table.selectRow(0)
    page._set_rule(d.MAP_SNOW)
    assert page.map_table.item(0, 2).text() == "Snow"
    page.map_table.selectRow(0)
    page._set_rule(d.MAP_AUTO)
    assert page.map_table.item(0, 2).text() == "Auto" and page.config().map_rules == {}


# ---- the forecast tells the truth (Dale: "forecast for a blizzard on a green deserty map") ----

def test_forecast_and_status_name_what_will_actually_fall():
    config = on(frequency="normal", weights=only("blizzard"))
    jungle = run(config, hours=3, map_is_snow=False)
    texts = " | ".join(dec.status for _, dec, _ in jungle)
    assert "storm on this map" in texts and "Storm front" in texts
    assert "blizzard" not in texts.lower()

    config = on(frequency="normal", weights=only("rain"))
    arctic = " | ".join(dec.status for _, dec, _ in run(config, hours=3, map_is_snow=True))
    assert "snow on this map" in arctic and "Snow front" in arctic and "rain" not in arctic.lower()

    by_hand = on(frequency="normal", weights=only("blizzard"), map_rules={"adale": d.MAP_NO_SNOW})
    assert "storm on this map" in " | ".join(dec.status for _, dec, _ in run(by_hand, hours=2, map_is_snow=True))


def test_kind_on_map():
    assert d.kind_on_map("blizzard", d.MAP_NO_SNOW) == "storm" and d.kind_on_map("snow", d.MAP_NO_SNOW) == "rain"
    assert d.kind_on_map("storm", d.MAP_SNOW) == "blizzard" and d.kind_on_map("drizzle", d.MAP_SNOW) == "snow"
    assert d.kind_on_map("fog", d.MAP_SNOW) == "fog" and d.kind_on_map("blizzard", d.MAP_AUTO) == "blizzard"
