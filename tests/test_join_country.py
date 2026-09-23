"""Where-from line for first-time joiners (2026-09-23): wording, UK nations,
VPN silent or flagged, one shared lookup, after the welcome, gives up quietly."""
import os
import sys

import pytest

from wolfrat import ip_checks as ic
from wolfrat import join_country as jc
from wolfrat.ip_checks import Verdict

NOW = 1_800_000_000.0
T = jc.DEFAULT_TEMPLATE


def v(**kw):
    return Verdict(kw.pop("ip", "81.1.1.1"), NOW, **kw)


# ---- wording ------------------------------------------------------------------

def test_uk_players_get_their_nation():
    d = jc.decide("Dale", "81.1.1.1", v(country="GB", country_name="United Kingdom", region="Scotland"), T)
    assert (d.state, d.text) == (jc.SAY, "Dale joined from Scotland")


def test_others_get_region_and_country_when_it_fits():
    d = jc.decide("Tex", "81.1.1.1", v(country="US", country_name="United States", region="Texas"), T)
    assert d.text == "Tex joined from Texas, United States"


def test_long_name_drops_the_region_before_cutting():
    name = "X" * 30
    d = jc.decide(name, "81.1.1.1", v(country="US", country_name="United States", region="North Carolina"), T)
    assert d.text == f"{name} joined from United States" and len(d.text) <= 62


def test_cache_from_before_regions_falls_back_to_country():
    d = jc.decide("Old", "81.1.1.1", v(country="GB", country_name="United Kingdom"), T)
    assert d.text == "Old joined from United Kingdom"


def test_accents_are_folded_for_game_chat():
    d = jc.decide("Hans", "81.1.1.1", v(country="DE", country_name="Germany", region="Baden-Württemberg"), T)
    assert d.text == "Hans joined from Baden-Wurttemberg, Germany"


def test_other_placeholders():
    d = jc.decide("Ann", "81.1.1.1", v(country="IE", country_name="Ireland", region="Leinster"),
                  "Say hi to {player} from {country} ({region})")
    assert d.text == "Say hi to Ann from Ireland (Leinster)"


# ---- when to keep quiet -----------------------------------------------------------

def test_vpn_is_silent_by_default_and_flagged_when_asked():
    vpn = v(proxy=True, kind="VPN", country="NL", country_name="Netherlands")
    assert jc.decide("Ivan", "81.1.1.1", vpn, T).state == jc.SKIP
    d = jc.decide("Ivan", "81.1.1.1", vpn, T, mention_vpn=True)
    assert (d.state, d.text) == (jc.SAY, "Ivan joined on a VPN")
    host = v(hosting=True, country="DE", country_name="Germany")      # datacentre = a VPN we weren't told about
    assert jc.decide("Ivan", "81.1.1.1", host, T).state == jc.SKIP


def test_failures_private_and_unknown():
    assert jc.decide("A", "", None, T).state == jc.WAIT
    assert jc.decide("A", "81.1.1.1", None, T).state == jc.WAIT
    assert jc.decide("A", "192.168.1.5", None, T).state == jc.SKIP
    assert jc.decide("A", "81.1.1.1", v(error="HTTP 429 (rate limit)"), T).state == jc.SKIP
    assert jc.decide("A", "81.1.1.1", v(), T).state == jc.SKIP          # no country in the answer


# ---- the queue ----------------------------------------------------------------------

class Rig:
    def __init__(self):
        self.now = NOW
        self.conn = {}            # name -> (ip, verdict)
        self.requests, self.said, self.logged = [], [], []
        self.a = jc.CountryAnnouncer(lambda n: self.conn.get(n, ("", None)), self.requests.append,
                                     self.said.append, self.logged.append, clock=lambda: self.now)

    def after(self, seconds):
        self.now += seconds
        self.a.tick()


def test_waits_for_the_welcome_then_says_it_once():
    r = Rig()
    r.conn["Dale"] = ("81.1.1.1", v(country="GB", country_name="United Kingdom", region="Scotland"))
    r.a.queue("Dale", 42)
    r.after(40)
    assert r.said == []                       # answer's in, welcome not out yet
    r.after(2)
    assert r.said == ["Dale joined from Scotland"] and r.a.pending() == 0
    r.after(2)
    assert r.said == ["Dale joined from Scotland"]
    assert r.a.last == 'Dale - said "Dale joined from Scotland"'


def test_keeps_asking_until_the_answer_lands_then_gives_up_quietly():
    r = Rig()
    r.a.queue("Slow", 40)
    r.after(10); r.after(30); r.after(30)
    assert r.requests.count("Slow") >= 3 and r.said == []
    r.conn["Slow"] = ("81.1.1.1", v(country="FR", country_name="France"))
    r.after(2)
    assert r.said == ["Slow joined from France"]
    r.a.queue("Never", 40)
    r.after(40 + jc.GIVE_UP_SECONDS)
    assert r.said == ["Slow joined from France"] and r.a.pending() == 0
    assert "Never - skipped" in r.a.last and "gave up" in r.a.last


def test_vpn_skip_is_logged_not_said():
    r = Rig()
    r.conn["Ivan"] = ("81.1.1.1", v(proxy=True, country="NL"))
    r.a.queue("Ivan", 40)
    r.after(40)
    assert r.said == [] and r.a.last == "Ivan - skipped - on a VPN - kept quiet"


# ---- the Bans tab: one shared lookup ------------------------------------------------------

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from wolfrat.bans_tab import BansTab  # noqa: E402
from wolfrat.jo_players import SlotInfo  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class FakeReader:
    def __init__(self):
        self.slots, self.status, self.available = [], "fake", True

    def read(self):
        return self.slots


class FakeChecker:
    def __init__(self):
        self.submitted = []

    def submit(self, ip, provider, key):
        if any(ip == done[0] for done in self.submitted):
            return False
        self.submitted.append((ip, provider, key)); return True

    def pending(self):
        return 0


def bans(tmp_path, clock):
    reader, checker = FakeReader(), FakeChecker()
    tab = BansTab(str(tmp_path), punt=lambda p, why: None, announce=lambda t: None, reader=reader,
                  clock=clock, checker=checker)
    return tab, reader, checker


def poll(tab, reader, *players):
    reader.slots = [SlotInfo(0, "Host", "")] + [SlotInfo(i + 1, n, ip) for i, (n, ip) in enumerate(players)]
    tab.on_players([{"id": str(i + 1), "name": n} for i, (n, _ip) in enumerate(players)])


def test_bans_tab_lookup_is_shared_and_never_doubled(tmp_path):
    now = [NOW]
    tab, reader, checker = bans(tmp_path, lambda: now[0])
    tab.checks_cb.setChecked(True)                   # checks on: the Bans tab looks everyone up
    poll(tab, reader, ("Newbie", "81.2.3.4"))
    assert [s[0] for s in checker.submitted] == ["81.2.3.4"]
    tab.look_up("Newbie"); tab.look_up("newbie ")   # the where-from line asking again
    assert len(checker.submitted) == 1
    assert tab.connection_for("Newbie") == ("81.2.3.4", None)
    tab._on_verdict(Verdict("81.2.3.4", now[0], country="GB", country_name="United Kingdom", region="Wales"))
    ip, verdict = tab.connection_for("Newbie")
    assert ip == "81.2.3.4" and verdict.region == "Wales"
    tab.look_up("Newbie")                            # cached: nothing sent
    assert len(checker.submitted) == 1


def test_checks_off_the_line_still_gets_one_lookup_without_kicking(tmp_path):
    now = [NOW]
    punts = []
    reader, checker = FakeReader(), FakeChecker()
    tab = BansTab(str(tmp_path), punt=lambda p, why: punts.append(p["name"]), announce=lambda t: None,
                  reader=reader, clock=lambda: now[0], checker=checker)
    poll(tab, reader, ("Ivan", "81.9.9.9"))
    assert checker.submitted == []                   # checks off: the Bans tab looks nobody up
    tab.look_up("Ivan")
    assert [s[0] for s in checker.submitted] == ["81.9.9.9"]
    tab._on_verdict(Verdict("81.9.9.9", now[0], proxy=True, kind="VPN", country="NL"))
    poll(tab, reader, ("Ivan", "81.9.9.9"))
    assert punts == []                               # a VPN answer never kicks while checks are off


def test_returning_player_guard(tmp_path):
    now = [NOW]
    tab, reader, _checker = bans(tmp_path, lambda: now[0])
    assert not tab.returning_player("Stranger")
    poll(tab, reader, ("Regular", "81.1.1.1"))
    assert not tab.returning_player("Regular")       # first visit, just now
    now[0] += 3600
    assert tab.returning_player("Regular")           # known for an hour


# ---- the Messages tab ------------------------------------------------------------------------

def test_messages_tab_queues_first_timers_after_the_welcome(qtbot, tmp_path):
    from wolfrat.app import MessagesTab
    from wolfrat.runtime import DesktopRuntime

    class Server:
        def _log(self, m): pass
        def announce(self, m): pass

    tab = MessagesTab(Server(), None, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(tab)
    assert not tab.country_cb.isChecked() and not tab.country_input.isEnabled()     # off by default
    now = [NOW]
    said = []
    tab.country._clock = lambda: now[0]
    tab.country._say = said.append
    conn = {"Dale": ("81.1.1.1", v(country="GB", country_name="United Kingdom", region="Scotland"))}
    tab.country._connection = lambda n: conn.get(n, ("", None))
    tab.country._request = lambda n: None

    tab.check_new_players([{"name": "Dale"}])
    assert tab.country.pending() == 0                # line off: nothing queued
    tab.country_cb.setChecked(True)
    tab.check_new_players([{"name": "Dale"}])        # already seen by the welcome list
    assert tab.country.pending() == 0
    tab._seen_players.clear()
    tab.check_new_players([{"name": "Dale"}])
    assert tab.country.pending() == 1
    now[0] += 42
    tab._country_tick()
    assert said == ["Dale joined from Scotland"] and tab.country_last.text().startswith("Last: Dale")
    cfg = MessagesTab(Server(), None, DesktopRuntime.isolated(tmp_path))
    qtbot.addWidget(cfg)
    assert cfg.country_cb.isChecked() and not cfg.country_vpn_cb.isChecked()      # saved
