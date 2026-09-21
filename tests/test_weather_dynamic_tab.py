"""The Dynamic page, driven through the real widgets against the fake server."""

import json

from PyQt6.QtCore import Qt

from tests.test_weather import FakeServer
from wolfrat import weather as w
from wolfrat import weather_dynamic as d
from wolfrat.runtime import DesktopRuntime
from wolfrat.weather import Addr
from wolfrat.weather_tab import WeatherTab

MAPS = [("TD-COD4_KillHouse.bms", "Kill House"), ("AS-DoslinOblast.bms", "Doslin Oblast"),
        ("TD-Adale.bms", "Adale City")]


class Handle:
    pid = 77
    folder = ""

    def exe_path(self):
        return str(self.folder / "jointops.exe") if self.folder else ""

    def close(self):
        pass


def make(qtbot, tmp_path, server, context=None):
    attaches, said = [], []
    Handle.folder = tmp_path / "server"
    Handle.folder.mkdir(exist_ok=True)

    def attach(writable=True):
        attaches.append(writable)
        controller = w.WeatherController(server)
        controller.verify()
        return controller, Handle()

    tab = WeatherTab(DesktopRuntime.isolated(tmp_path), send_chat=said.append, attach=attach,
                     context=context, map_list=lambda: MAPS)
    qtbot.addWidget(tab)
    tab._timer.stop()
    tab._dynamic._rng = __import__("random").Random(1)      # same dice every run: no flaky tests
    tab._poll()
    return tab, said, attaches


def only_storms(page):
    for name, (box, _) in page._type_widgets.items():
        box.setChecked(name == "storm")
    page.frequency_combo.setCurrentIndex(page.frequency_combo.findData("constant"))


def test_off_by_default_and_never_writes(qtbot, tmp_path):
    server = FakeServer()
    tab, _, attaches = make(qtbot, tmp_path, server)
    for _ in range(5):
        tab._poll()
    assert attaches == [False] and server.writes == []
    assert "off" in tab.dynamic_page.status_lbl.text()


def test_a_storm_rolls_in_on_its_own_and_everything_is_saved(qtbot, tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer()
    tab, said, attaches = make(qtbot, tmp_path, server)
    tab._dynamic._clock = lambda: clock[0]
    page = tab.dynamic_page
    only_storms(page)
    page.enabled_cb.setChecked(True)
    assert attaches[-1] is True

    peak = 0
    for _ in range(int(40 * 60 / 5)):
        clock[0] += 5
        server.tick(5)
        tab._poll()
        peak = max(peak, server.client_sees()["precip"])
    assert peak > 130
    assert any("moving in" in line or "clouding over" in line for line in said)
    assert "front" in page.status_lbl.text() or "Clear" in page.status_lbl.text()

    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())["dynamic"]
    assert saved["enabled"] is True and saved["frequency"] == "constant"
    assert saved["weights"]["storm"] > 0 and saved["weights"]["rain"] == 0

    # switching it off mid-front hands the sky back to the map
    page.enabled_cb.setChecked(False)
    server.tick(180)
    assert server.client_sees()["precip"] == 0 and server.peek(Addr.FOG_TARGET) == 1000 << 16


def test_manual_weather_wins_then_dynamic_carries_on(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server)
    tab._dynamic._clock = lambda: clock[0]
    tab._schedule._clock = lambda: clock[0]
    only_storms(tab.dynamic_page)
    tab.dynamic_page.enabled_cb.setChecked(True)
    tab.minutes_spin.setValue(1)
    tab._start_preset("fog")
    tab._poll()
    assert "manual" in tab.dynamic_page.status_lbl.text().lower()
    assert server.peek(Addr.FOG_TARGET) == 150 << 16
    clock[0] += 61
    tab._poll()
    clock[0] += 5
    tab._poll()
    assert "manual" not in tab.dynamic_page.status_lbl.text().lower()


def test_nothing_is_written_while_the_server_is_loading_a_map(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    info = {"connected": True, "map": "TD-Adale.bms", "players": 6}
    server = FakeServer()
    tab, _, _ = make(qtbot, tmp_path, server, context=lambda: dict(info))
    tab._dynamic._clock = lambda: clock[0]
    only_storms(tab.dynamic_page)
    tab.dynamic_page.enabled_cb.setChecked(True)
    for _ in range(60):                                  # five minutes: a front is under way
        clock[0] += 5
        server.tick(5)
        tab._poll()
    assert server.writes

    info["connected"] = False                            # map change: admin port drops
    server.writes.clear()
    for _ in range(6):
        clock[0] += 5
        tab._poll()
    info.update(connected=True, map="AS-DoslinOblast.bms")
    for _ in range(3):                                   # back, but not settled yet
        clock[0] += 5
        tab._poll()
    assert server.writes == []
    assert "Waiting" in tab.dynamic_page.status_lbl.text()

    server.poke(Addr.PRECIP_TARGET, 0)                   # the load reset the sky
    server.poke(Addr.OVERCAST_TARGET, 0)
    for _ in range(3):
        clock[0] += 5
        tab._poll()
    assert server.writes, "the front should be put back once the server has settled"


def test_map_rules_by_list_and_by_typed_name(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, FakeServer())
    page = tab.dynamic_page
    assert page.map_table.rowCount() == 3

    page.map_search.setText("cod kill house")
    assert page.map_table.rowCount() == 1
    assert "KillHouse" in page.map_table.item(0, 0).text()
    page.map_table.selectRow(0)
    page._set_rule(d.MAP_NONE)
    assert page.config().map_rules == {"TD-COD4_KillHouse.bms": d.MAP_NONE}

    page.map_search.setText("frozen lake")               # not on the server (yet)
    assert page.map_table.rowCount() == 0
    page._set_rule(d.MAP_SNOW)
    rules = page.config().map_rules
    assert rules["frozen lake"] == d.MAP_SNOW
    assert d.map_rule_for("AS-Frozen_Lake.bms", rules) == d.MAP_SNOW

    page.map_search.setText("")
    page.only_rules_cb.setChecked(True)
    assert page.map_table.rowCount() == 2
    page.map_table.selectAll()
    page._set_rule(d.MAP_NORMAL)
    assert page.config().map_rules == {}
    saved = json.loads((tmp_path / "wolfrat_weather.json").read_text())["dynamic"]
    assert saved["map_rules"] == {}


def test_lightning_is_reported_honestly_and_fires_only_with_the_patch(qtbot, tmp_path):
    server = FakeServer()
    server.poke(Addr.LIGHTNING_MARKER, 0)
    server.poke(Addr.LIGHTNING_MAILBOX, 0)
    tab, _, _ = make(qtbot, tmp_path, server)
    assert "Install lightning add-on" in tab.dynamic_page.lightning_lbl.text()
    tab.dynamic_page.enabled_cb.setChecked(True)
    tab._poll(), tab._poll()
    assert "Install lightning add-on" in tab.dynamic_page.lightning_lbl.text()
    assert w.WeatherController(server).flash() is False
    assert server.peek(Addr.LIGHTNING_MAILBOX) == 0

    server.poke(Addr.LIGHTNING_MARKER, w.LIGHTNING_MAGIC)
    tab._poll()
    assert "ready" in tab.dynamic_page.lightning_lbl.text()
    assert w.WeatherController(server).flash() is True
    assert server.peek(Addr.LIGHTNING_MAILBOX) == 1


# ---- Trench Warfare: a map that scripts its own rain ------------------------

def trench_warfare(server):
    """AS-TrenchWarfare.WAC: fogdist(350) Rain(100) skyspeed(50), once, at map start."""
    server.set_map("AS - Trench Warfare", "dvxg3.trn")
    server.poke(Addr.PRECIP_TARGET, 0x10000), server.poke(Addr.PRECIP_CURRENT, 0x10000)
    server.poke(Addr.FOG_TARGET, 350 << 16), server.poke(Addr.FOG_CURRENT, 350 << 16)
    server.poke(Addr.CLOUD_SPEED_TARGET, 50 << 10)


def test_a_maps_own_rain_is_left_alone_and_the_status_says_so(qtbot, tmp_path):
    server = FakeServer()
    trench_warfare(server)
    tab, _, _ = make(qtbot, tmp_path, server)
    tab.dynamic_page.enabled_cb.setChecked(True)
    for _ in range(10):
        server.tick(5)
        tab._poll()
    assert server.client_sees()["precip"] == 255 and server.client_sees()["fog_m"] == 350
    text = tab.dynamic_page.status_lbl.text()
    assert "No WolfRAT front right now" in text and "the map\'s own sky right now: rain 100%" in text
    assert "Clear" not in text


def test_after_our_front_the_maps_own_rain_comes_back(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer()
    trench_warfare(server)
    tab, _, _ = make(qtbot, tmp_path, server)
    tab._dynamic._clock = lambda: clock[0]
    tab._dynamic._rng = __import__("random").Random(7)       # same dice every run
    page = tab.dynamic_page
    for name, (box, _) in page._type_widgets.items():
        box.setChecked(name == "fog")                        # a front with no rain in it
    page.frequency_combo.setCurrentIndex(page.frequency_combo.findData("constant"))
    page.enabled_cb.setChecked(True)

    dried_out = False
    for _ in range(int(40 * 60 / 5)):
        clock[0] += 5
        server.tick(5)
        tab._poll()
        front = tab._dynamic.front
        if front is not None and server.client_sees()["precip"] == 0:
            dried_out = True                                 # our fog front replaced the rain
        if dried_out and front is None:
            break
    assert dried_out, "the fog front never took over"
    # handed back AS FOUND (the map script's rain), not as the .env says (no rain)
    assert server.peek(Addr.PRECIP_TARGET) == 0x10000
    assert server.peek(Addr.FOG_TARGET) == 350 << 16
    assert server.peek(Addr.CLOUD_SPEED_TARGET) == 50 << 10
    server.tick(30)                                          # and it really does come back
    assert server.client_sees()["precip"] > 60


def test_snapshot_and_restore_round_trip():
    server = FakeServer()
    trench_warfare(server)
    control = w.WeatherController(server)
    saved = control.snapshot()
    control.apply(w.PRESETS["fog"])
    server.tick(60)
    assert server.client_sees()["precip"] == 0 and server.client_sees()["fog_m"] == 150
    control.restore(saved, 20)
    server.tick(40)
    assert server.client_sees()["precip"] == 255 and server.client_sees()["fog_m"] == 350
