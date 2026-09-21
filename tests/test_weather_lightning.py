"""Lightning through the server.wac add-on."""

import random

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make, only_storms
from wolfrat import weather as w


def handshake(server, control):
    control.probe_addon()
    server.run_script()
    return control.probe_addon()


def test_the_shipped_script_is_the_one_the_controller_talks_to():
    script = w.ADDON_SCRIPT
    assert "if eq(G250, 1) then\nflash\nset(G250, 0)\nendif" in script
    assert "if eq(G250, 2) then\nfarflash\nset(G250, 0)\nendif" in script
    assert "if eq(G250, 3) then\nset(G250, 4)\nendif" in script
    assert w.ADDON_REQUEST == 0xC6BA40 + 1000


def test_handshake_finds_the_addon_and_flashes():
    server = FakeServer(addon=True)
    control = w.WeatherController(server)
    assert control.lightning_available() is False           # not asked yet
    assert handshake(server, control) is True and control.lightning_available()
    assert control.flash() is True
    server.run_script()
    assert control.flash(far=True) is True
    server.run_script()
    assert server.flashes == ["flash", "farflash"]
    assert server.peek(w.ADDON_REQUEST) == 0


def test_no_addon_means_no_flash_and_no_stray_writes_left_behind():
    server = FakeServer(addon=False)
    control = w.WeatherController(server)
    assert handshake(server, control) is False
    assert control.flash() is False and server.flashes == []
    assert not control.lightning_available()


def test_addon_disappears_after_a_map_without_it_and_comes_back():
    server = FakeServer(addon=True)
    control = w.WeatherController(server)
    assert handshake(server, control) is True
    server.addon = False
    server.poke(w.ADDON_REQUEST, 0)
    assert handshake(server, control) is False
    server.addon = True
    assert handshake(server, control) is True


def test_a_ping_never_stamps_on_a_flash_that_is_still_waiting():
    server = FakeServer(addon=True)
    control = w.WeatherController(server)
    handshake(server, control)
    control.flash()
    control.probe_addon()                                    # poll lands before the script ran
    assert server.peek(w.ADDON_REQUEST) == w.ADDON_FLASH
    server.run_script()
    assert server.flashes == ["flash"]
    control.probe_addon(), server.run_script()
    assert control.probe_addon() is True


def test_storms_flash_through_the_tab_and_the_buttons_light_up(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer(addon=True)
    tab, _, _ = make(qtbot, tmp_path, server)
    tab._dynamic._clock = lambda: clock[0]
    tab._dynamic._rng = random.Random(4)
    assert not tab.flash_btn.isEnabled()
    only_storms(tab.dynamic_page)
    tab.dynamic_page.enabled_cb.setChecked(True)
    for _ in range(int(45 * 60 / 5)):
        clock[0] += 5
        server.tick(5)
        tab._poll()
    assert tab.flash_btn.isEnabled() and "ready" in tab.dynamic_page.lightning_lbl.text()
    assert "ready" in tab.flash_lbl.text() and not tab.install_btn.isVisibleTo(tab)
    assert len(server.flashes) > 5 and {"flash", "farflash"} <= set(server.flashes)

    before = len(server.flashes)
    tab._flash("Dale")
    server.run_script()
    assert len(server.flashes) == before + 1
    tab.mods_cb.setChecked(True)
    assert tab.on_mod_command("Dale", "!lightning", []) is None


def test_without_the_addon_the_tab_says_so_and_mods_are_told(qtbot, tmp_path):
    server = FakeServer(addon=False)
    tab, _, _ = make(qtbot, tmp_path, server)
    tab.dynamic_page.enabled_cb.setChecked(True)
    for _ in range(3):
        server.tick(5)
        tab._poll()
    assert not tab.flash_btn.isEnabled()
    assert "Install lightning add-on" in tab.flash_lbl.text()
    assert tab.install_btn.isVisibleTo(tab)
    tab.mods_cb.setChecked(True)
    assert tab.on_mod_command("Dale", "!lightning", []) == "Lightning is not set up on this server."


# ---- the Install button: nobody should have to copy a file by hand ---------

def test_install_writes_a_script_the_2004_compiler_can_read(tmp_path):
    assert w.addon_installed(tmp_path) is False
    assert w.install_addon(tmp_path) == "installed"
    data = (tmp_path / "server.wac").read_bytes()
    assert data.count(b"\r\n") >= 15
    assert b"\n" not in data.replace(b"\r\n", b""), "a bare LF turns the whole file into one comment"
    assert b"if eq(G250, 1) then\r\nflash\r\n" in data
    assert w.addon_installed(tmp_path) is True
    assert w.install_addon(tmp_path) == "already"
    assert (tmp_path / "server.wac").read_bytes() == data


def test_install_keeps_an_admins_own_server_wac(tmp_path):
    (tmp_path / "server.wac").write_bytes(b"// my own rules\nif never then\ntext(hello)\nendif")
    assert w.install_addon(tmp_path) == "installed"
    data = (tmp_path / "server.wac").read_bytes()
    assert data.startswith(b"// my own rules\r\nif never then\r\ntext(hello)\r\nendif\r\n")
    assert b"WolfRAT weather add-on" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert not list(tmp_path.glob("*.wolfrat-new"))


def test_install_button_walks_the_admin_through_it(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer(addon=False)
    tab, _, _ = make(qtbot, tmp_path, server)
    tab._start_preset("rain")                                  # any button: WolfRAT may now write
    server.tick(5), tab._poll(), server.tick(5), tab._poll()
    assert tab.install_btn.isVisibleTo(tab) and not tab.flash_btn.isEnabled()

    tab._install_addon()
    assert (tmp_path / "server" / "server.wac").exists()
    assert not tab.install_btn.isVisibleTo(tab)
    assert "next map change" in tab.flash_lbl.text()
    assert "installed" in tab.log_list.item(tab.log_list.count() - 1).text().lower()

    server.addon = True                                        # the map changes: script loads
    server.poke(w.ADDON_REQUEST, 0)
    for _ in range(3):
        server.tick(5)
        tab._poll()
    assert tab.flash_btn.isEnabled() and "ready" in tab.flash_lbl.text()


def test_a_folder_wolfrat_cannot_write_to_says_what_to_do(tmp_path):
    import pytest
    blocked = tmp_path / "nope" / "deeper"
    with pytest.raises(w.WeatherError, match="administrator"):
        w.install_addon(blocked)
