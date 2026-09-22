"""'Link WolfRAT to the server': the Server tab's checklist and one-click set-up,
driven through the real Weather tab against the fake server."""

import json

from tests.test_weather import FakeServer
from wolfrat import server_link as sl
from wolfrat import weather as w
from wolfrat.runtime import DesktopRuntime
from wolfrat.server_link_panel import LINKED_TEXT, ServerLinkPanel
from wolfrat.weather_tab import WeatherTab


class Handle:
    pid = 5120

    def __init__(self, folder):
        self.folder = folder

    def exe_path(self):
        return str(self.folder / "jointops.exe")

    def close(self):
        pass


def make(qtbot, tmp_path, server=None, fail=None, connected=True):
    folder = tmp_path / "server"
    folder.mkdir(exist_ok=True)
    attaches = []
    ctx = {"connected": connected, "map": "AS-DoslinOblast.bms", "players": 3}

    def attach(writable=True):
        attaches.append(writable)
        if fail:
            raise fail
        controller = w.WeatherController(server)
        controller.verify()
        return controller, Handle(folder)

    tab = WeatherTab(DesktopRuntime.isolated(tmp_path), attach=attach, context=lambda: ctx)
    qtbot.addWidget(tab)
    tab._timer.stop()
    logged = []
    panel = ServerLinkPanel(tab.link_state, tab.link_to_server, recheck=tab._poll,
                            log=logged.append)
    qtbot.addWidget(panel)
    tab.link_changed.connect(panel.refresh)
    return tab, panel, folder, attaches, logged


def rows(panel):
    return [(m.text(), t.text(), d.text()) for m, t, d in panel._rows]


def settle(tab, server, clock, polls=6):
    for _ in range(polls):
        clock[0] += 5
        server.tick(5)
        tab._poll()


# ---- what a first-time admin sees ------------------------------------------

def test_before_looking_it_says_it_is_looking(qtbot, tmp_path):
    _, panel, _, _, _ = make(qtbot, tmp_path, FakeServer())
    assert "Looking" in rows(panel)[0][1]
    assert not panel.link_btn.isEnabled() and not panel.badge_lbl.isVisibleTo(panel)


def test_no_server_on_this_pc_explains_and_blames_nobody(qtbot, tmp_path):
    tab, panel, _, _, _ = make(qtbot, tmp_path, fail=w.ServerNotRunning("No jointops.exe..."))
    tab._poll()
    assert rows(panel)[0][:2] == ("✖", "Game server not found on this PC")
    assert not panel.link_btn.isEnabled()
    assert "another computer" in panel.hint_lbl.text()
    assert "works as normal" in panel.hint_lbl.text()


def test_a_server_it_cannot_use_says_why(qtbot, tmp_path):
    why = "Windows would not let WolfRAT open the server process. Run WolfRAT as administrator too."
    tab, panel, _, _, _ = make(qtbot, tmp_path, fail=w.WeatherError(why))
    tab._poll()
    table = rows(panel)
    assert table[0][1] == "Server found"
    assert table[1][:2] == ("✖", "WolfRAT cannot use it") and "administrator" in table[1][2]
    assert not panel.link_btn.isEnabled()


def test_found_and_reachable_offers_the_big_green_button(qtbot, tmp_path):
    server = FakeServer()
    tab, panel, folder, attaches, _ = make(qtbot, tmp_path, server)
    tab._poll()
    table = rows(panel)
    assert table[0][1] == "Server found" and "process 5120" in table[0][2] and str(folder) in table[0][2]
    assert table[1][:2] == ("✔", "WolfRAT can reach it")
    assert table[2][1:] == ("Server script", "Not installed yet.")
    assert panel.link_btn.isEnabled() and panel.link_btn.isVisibleTo(panel)
    assert not panel.badge_lbl.isVisibleTo(panel)
    assert "Players download nothing" in panel.hint_lbl.text()
    # looking still never takes a writable handle or touches the folder
    assert attaches == [False] and server.writes == [] and not (folder / "server.wac").exists()


# ---- one click ---------------------------------------------------------------

def test_one_click_links_and_the_badge_turns_green(qtbot, tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("wolfrat.weather_tab.time.monotonic", lambda: clock[0])
    server = FakeServer(addon=False)
    tab, panel, folder, attaches, logged = make(qtbot, tmp_path, server)
    tab._poll()

    panel.link_btn.click()
    assert attaches[-1] is True
    assert w.addon_installed(folder)
    assert panel.badge_lbl.isVisibleTo(panel) and panel.badge_lbl.text() == LINKED_TEXT
    assert not panel.link_btn.isVisibleTo(panel)
    assert "Linked" in logged[-1]
    assert json.loads((tmp_path / "wolfrat_weather.json").read_text())["linked"] is True

    settle(tab, server, clock)                      # this map loaded before the script
    assert rows(panel)[2][1] == "Server script installed"
    assert "next map change" in rows(panel)[2][2]

    server.addon = True                             # map change: the script is running
    server.poke(w.ADDON_REQUEST, 0)
    settle(tab, server, clock, polls=3)
    assert rows(panel)[2][:2] == ("✔", "Server script running")
    assert "Lightning" in rows(panel)[2][2]


def test_an_admins_own_server_wac_survives_the_link(qtbot, tmp_path):
    server = FakeServer()
    tab, panel, folder, _, _ = make(qtbot, tmp_path, server)
    (folder / "server.wac").write_bytes(b"// my rules\r\n")
    tab._poll()
    panel.link_btn.click()
    assert (folder / "server.wac").read_bytes().startswith(b"// my rules\r\n")
    panel.link_btn.click()                           # pressing again is harmless
    assert (folder / "server.wac").read_bytes().count(b"WolfRAT weather add-on") == 1


def test_a_failed_link_stays_readable_under_the_button(qtbot, tmp_path, monkeypatch):
    server = FakeServer()
    tab, panel, _, _, logged = make(qtbot, tmp_path, server)
    tab._poll()

    def refuse(_folder):
        raise w.WeatherError("WolfRAT could not write server.wac. Run WolfRAT as administrator.")

    monkeypatch.setattr(w, "install_addon", refuse)
    panel.link_btn.click()
    assert "administrator" in panel.hint_lbl.text() and "administrator" in logged[-1]
    assert panel.link_btn.isEnabled()
    tab._poll()                                      # the next poll does not wipe it
    assert "administrator" in panel.hint_lbl.text()


def test_waits_for_the_admin_connection_before_promising_a_check(qtbot, tmp_path):
    server = FakeServer()
    tab, panel, folder, _, _ = make(qtbot, tmp_path, server, connected=False)
    w.install_addon(folder)
    tab._poll(), tab._poll()
    assert panel.badge_lbl.isVisibleTo(panel)
    assert "once you are connected" in rows(panel)[2][2]


# ---- people who installed the add-on before linking existed -------------------

def test_an_add_on_from_the_weather_tab_counts_as_linked(qtbot, tmp_path):
    server = FakeServer()
    tab, panel, folder, attaches, _ = make(qtbot, tmp_path, server)
    w.install_addon(folder)
    tab._poll()
    assert panel.badge_lbl.isVisibleTo(panel)
    tab._poll()
    assert attaches[-1] is True                      # linked: the script check can run
    assert json.loads((tmp_path / "wolfrat_weather.json").read_text())["linked"] is True


def test_server_closing_takes_the_badge_away(qtbot, tmp_path):
    server = FakeServer()
    tab, panel, folder, _, _ = make(qtbot, tmp_path, server)
    w.install_addon(folder)
    tab._poll()
    assert panel.badge_lbl.isVisibleTo(panel)
    tab._attach = lambda writable=True: (_ for _ in ()).throw(w.ServerNotRunning("gone"))
    tab._problem("Could not read the server's memory (has it closed?).")
    tab._poll()
    assert not panel.badge_lbl.isVisibleTo(panel)
    assert rows(panel)[0][1] == "Game server not found on this PC"


def test_describe_never_calls_it_linked_without_the_script():
    state = sl.LinkState(looked=True, found=True, reachable=True, pid=1, folder="C:/JO")
    assert sl.describe(state).mode == "link"
    assert sl.describe(sl.LinkState(looked=True, found=True, reachable=True,
                                    script_installed=True)).mode == "linked"
