"""Hunting sharks: entity-table scan, live writes, and the Wildlife page."""

import struct

import pytest

from tests.test_weather import FakeServer
from tests.test_weather_tab import make
from wolfrat import wildlife as wl
from wolfrat.weather import Addr as WAddr


REGION = 0xA80000
ENTITIES = 0xB00000
STRIDE = 0x200
NAMES = 0xB40000
AI = 0xB50000


class FlatMemory:
    """One flat byte region, so reads at any offset work like the real process."""

    def __init__(self):
        self.buf = bytearray(0x100000)
        self.writes = []

    def _off(self, address, size):
        off = address - REGION
        if off < 0 or off + size > len(self.buf):
            raise wl.WildlifeError("unmapped")
        return off

    def read(self, address, size):
        off = self._off(address, size)
        return bytes(self.buf[off:off + size])

    def write(self, address, data):
        off = self._off(address, len(data))
        self.buf[off:off + len(data)] = data
        self.writes.append((address, bytes(data)))

    def u32(self, address, value):
        self.write(address, struct.pack("<I", value))


def make_server(kinds=("Shark", "Shark", "Tiger", "Shark"), attack_m=100, team=0):
    mem = FlatMemory()
    mem.u32(wl.Addr.ENTITY_BASE, ENTITIES)
    mem.u32(wl.Addr.ENTITY_STRIDE, STRIDE)
    mem.u32(wl.Addr.ENTITY_USED, len(kinds) + 1)          # +1: an empty slot
    for i, kind in enumerate(kinds):
        ent = ENTITIES + i * STRIDE
        name = NAMES + i * 16
        mem.write(name, kind.encode() + b"\0")
        mem.u32(ent + wl.ENT_NAME_PTR, name)
        ai = AI + i * 0x100
        mem.u32(ent + wl.ENT_AI_PTR, ai)
        mem.write(ent + wl.ENT_TEAM, bytes([team]))
        mem.write(ent + wl.ENT_HP, struct.pack("<h", 2100 - i))
        mem.u32(ai + wl.AI_ATTACK_DIST, attack_m << 16)
    mem.writes.clear()
    return mem


def test_scan_finds_only_sharks_with_their_numbers():
    mem = make_server()
    sharks = wl.scan(mem)
    assert [s.address for s in sharks] == [ENTITIES, ENTITIES + STRIDE, ENTITIES + 3 * STRIDE]
    assert all(s.team == 0 and s.attack_m == 100 and not s.hunting for s in sharks)
    assert [s.hp for s in sharks] == [2100, 2099, 2097]


def test_scan_rejects_a_table_that_does_not_look_right():
    mem = make_server()
    mem.u32(wl.Addr.ENTITY_STRIDE, 4)
    with pytest.raises(wl.WildlifeError, match="does not look right"):
        wl.scan(mem)
    with pytest.raises(wl.WildlifeError, match="Could not read"):
        wl.scan(FakeServer())                 # sparse fake: the entity table is unmapped


def test_hunt_writes_team_and_attack_only_where_needed():
    mem = make_server()
    sharks = wl.scan(mem)
    assert wl.hunt(mem, sharks, 10) == 3
    assert len(mem.writes) == 6
    assert mem.read(ENTITIES + wl.ENT_TEAM, 1) == b"\x03"
    assert struct.unpack("<I", mem.read(AI + wl.AI_ATTACK_DIST, 4))[0] == 10 << 16
    assert mem.read(ENTITIES + 2 * STRIDE + wl.ENT_TEAM, 1) == b"\x00"     # the tiger
    mem.writes.clear()
    assert wl.hunt(mem, wl.scan(mem), 10) == 0 and mem.writes == []


def test_keeper_states_and_reapply_after_respawn():
    mem = make_server()
    keeper = wl.SharkKeeper()
    tick = lambda **kw: keeper.tick(mem, map_name="hookah1", linked=True, attack_m=10,
                                    **{"enabled": True, "writable": True, **kw})
    assert "Not linked" in keeper.tick(mem, enabled=True, attack_m=10, writable=True,
                                       map_name=None, linked=False).text
    off = tick(enabled=False)
    assert "3 sharks on hookah1" in off.text and "hunting is off" in off.text and not off.hooked
    assert mem.writes == []
    assert "cannot write" in tick(writable=False).text
    on = tick()
    assert on.hooked and on.text.startswith("🟢 3 sharks hooked on hookah1")
    assert "Re-applied 3x" in on.text and on.reapplied == 3
    again = tick()
    assert again.hooked and again.reapplied == 3            # nothing drifted
    # a respawn puts the map's numbers back on one shark
    mem.write(ENTITIES + wl.ENT_TEAM, b"\x00")
    mem.u32(AI + wl.AI_ATTACK_DIST, 100 << 16)
    assert tick().reapplied == 4
    # the slider moved: every shark gets the new distance
    moved = keeper.tick(mem, enabled=True, attack_m=12, writable=True, map_name="hookah1", linked=True)
    assert moved.reapplied == 7 and "12 m" in moved.text


def test_keeper_no_sharks_and_unreadable_table():
    keeper = wl.SharkKeeper()
    mem = make_server(kinds=("Tiger",))
    text = keeper.tick(mem, enabled=True, attack_m=10, writable=True, map_name="x", linked=True).text
    assert text.startswith("No sharks on x")
    text = keeper.tick(FakeServer(), enabled=True, attack_m=10, writable=True, map_name="x", linked=True).text
    assert "Could not read" in text


# ---------------------------------------------------------------- the page

def _table_with_no_sharks(server):
    server.poke(wl.Addr.ENTITY_BASE, ENTITIES)
    server.poke(wl.Addr.ENTITY_STRIDE, STRIDE)
    server.poke(wl.Addr.ENTITY_USED, 0)
    return server


def test_wildlife_is_the_third_page_and_says_tac_only(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    assert tab.pages.tabText(2) == "🦈 Wildlife" and tab.pages.widget(2) is tab.wildlife_page
    assert "OscarMike247" in tab.wildlife_page.banner_lbl.text()
    assert "TAC mod only" in tab.wildlife_page.banner_lbl.text()
    assert tab.wildlife_page.status_lbl.text().startswith("No sharks")
    assert tab.wildlife_page.enabled_cb.isEnabled()
    assert not tab.wildlife_page.enabled()                   # off by default


def test_no_server_greys_the_wildlife_page_too(qtbot, tmp_path):
    tab, _, _ = make(qtbot, tmp_path, fail="No jointops.exe is running on this PC.")
    assert "No jointops.exe" in tab.wildlife_page.status_lbl.text()
    assert not tab.wildlife_page.enabled_cb.isEnabled()


def test_switching_on_asks_for_a_writable_handle_and_saves(qtbot, tmp_path):
    tab, _, attaches = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    assert attaches == [False]
    tab.wildlife_page.enabled_cb.setChecked(True)
    assert attaches == [False, True]
    saved = (tmp_path / "wolfrat_weather.json").read_text(encoding="utf-8")
    assert '"enabled": true' in saved and '"attack_m": 10' in saved
    assert any("Hunting sharks switched on" in tab.log_list.item(i).text()
               for i in range(tab.log_list.count()))


def test_wildlife_page_has_two_scrolling_columns(qtbot, tmp_path):
    from PyQt6.QtWidgets import QScrollArea
    tab, _, _ = make(qtbot, tmp_path, _table_with_no_sharks(FakeServer()))
    columns = [a for a in tab.wildlife_page.findChildren(QScrollArea) if a.widgetResizable()]
    assert len(columns) == 2
