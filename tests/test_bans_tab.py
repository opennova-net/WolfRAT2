"""The Bans tab, driven offscreen: ban from the online table, punt on sight with
cooldown, rejoin caught by IP, whitelist wins, firewall copy, save + reload,
name bans still work when IPs cannot be read (2026-09-22)."""
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from wolfrat import ban_rules as br
from wolfrat.bans_tab import BansTab
from wolfrat.jo_players import SlotInfo

_app = QApplication.instance() or QApplication(sys.argv[:1])
NOW = 1_800_000_000.0


class FakeReader:
    def __init__(self):
        self.slots, self.status, self.available = [], "fake", True

    def read(self):
        return self.slots


class Rig:
    def __init__(self, folder):
        self.now = NOW
        self.punts, self.said, self.logged = [], [], []
        self.reader = FakeReader()
        self.folder = str(folder)
        self.tab = self.build()

    def build(self):
        return BansTab(self.folder, punt=lambda p, why: self.punts.append((p["name"], why)),
                       announce=self.said.append, log=self.logged.append, reader=self.reader,
                       clock=lambda: self.now, admin_name="Dale")

    def poll(self, *players, seconds=5):
        self.now += seconds
        self.reader.slots = [SlotInfo(0, "Host", "")] + [SlotInfo(i + 1, n, ip) for i, (n, ip) in enumerate(players)]
        self.tab.on_players([{"id": str(i + 1), "name": n} for i, (n, _ip) in enumerate(players)])


def test_ban_from_the_online_table_then_punt_on_sight_with_cooldown(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    assert rig.punts == [] and rig.tab.online_table.item(1, 1).text() == "5.6.7.8"
    rig.tab.online_table.selectRow(1)
    rig.tab.online_reason.setText("griefing")
    rig.tab.online_expiry.setCurrentIndex(3)          # 7 days
    rig.tab._ban_selected("both")
    assert [(e.kind, e.value) for e in rig.tab.bans.entries] == [("name", "Troll"), ("ip", "5.6.7.8")]
    assert rig.tab.bans.entries[0].expires_at == rig.now + 7 * 86400
    assert rig.punts == [("Troll", "Banned: griefing")]
    rig.punts.clear()
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))
    assert rig.punts == [("Troll", "Banned: griefing")] and rig.said == ["Troll removed - banned: griefing"]
    rig.poll(("Dale", "82.68.58.92"), ("Troll", "5.6.7.8"))            # 10 s later: cooldown
    assert len(rig.punts) == 1
    assert rig.tab.bans.entries[0].hits == 1 and rig.tab.bans.entries[0].last_hit_ip == "5.6.7.8"


def test_rejoin_under_a_new_name_is_caught_by_ip_and_shown_as_an_alias(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab.ban_now("Troll", reason="griefing", kind="both")
    rig.punts.clear()
    rig.poll(("NewName", "5.6.7.8"), seconds=60)
    assert rig.punts == [("NewName", "Banned: griefing")]
    assert rig.tab.online_table.item(0, 3).text() == "Troll"


def test_whitelist_beats_a_range_and_the_tab_says_so_in_its_log(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("5.6.*"); rig.tab.add_reason.setText("bad range"); rig.tab._add_typed()
    rig.tab.white_value.setText("5.6.7.8"); rig.tab.white_note.setText("Ham's home"); rig.tab._add_white()
    rig.poll(("Ham", "5.6.7.8"), ("Other", "5.6.9.9"))
    assert rig.punts == [("Other", "Banned: bad range")]
    assert any("Whitelisted ip 5.6.7.8" in line for line in rig.logged)


def test_firewall_copy_needs_an_ip_row_and_puts_the_powershell_line_on_the_clipboard(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("Troll"); rig.tab._add_typed()
    rig.tab.add_value.setText("82.68.*"); rig.tab._add_typed()
    rows = {rig.tab.ban_table.item(r, 0).text(): r for r in range(rig.tab.ban_table.rowCount())}
    rig.tab.ban_table.selectRow(rows["name"])
    assert not rig.tab.copy_block_btn.isEnabled()
    rig.tab.ban_table.selectRow(rows["ip"])
    assert rig.tab.copy_block_btn.isEnabled()
    rig.tab._copy_firewall(True)
    assert _app.clipboard().text() == br.firewall_block_command("82.68.*")
    rig.tab._copy_firewall(False)
    assert _app.clipboard().text() == br.firewall_unblock_command("82.68.*")


def test_bad_typed_value_is_refused_with_the_reason(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.add_value.setText("999.1.1.1"); rig.tab._add_typed()
    assert rig.tab.bans.entries == [] and rig.logged[-1] == "Not added: not a valid IP address"


def test_chat_and_history_survive_a_restart_and_unban_works(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(("Troll", "5.6.7.8"))
    rig.tab.on_chat([{"id": 1, "text": "Troll: old"}])                      # first batch skipped
    rig.tab.on_chat([{"id": 1, "text": "Troll: old"}, {"id": 2, "text": "Troll: u all suck"}])
    rig.tab.ban_now("Troll", reason="mouth", kind="name", added_by="Ham")
    again = rig.build()
    assert [(e.value, e.reason, e.added_by) for e in again.bans.entries] == [("Troll", "mouth", "Ham")]
    assert list(again.history.get("Troll").chat) == [[rig.now, "u all suck"]]
    assert again.history.get("troll").ips == {"5.6.7.8": [1, rig.now]}
    assert again.unban("troll") == 1 and again.unban("troll") == 0


def test_without_the_server_on_this_pc_name_bans_still_work_and_status_says_why(tmp_path):
    rig = Rig(tmp_path)
    rig.reader.available = False
    rig.reader.status = "jointops.exe is not running on this PC - IPs need the server on the same machine as WolfRAT."
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=NOW))
    rig.now += 5
    rig.reader.slots = []
    rig.tab.on_players([{"id": "9", "name": "troll"}])
    assert rig.punts == [("troll", "Banned: Troll")]
    assert rig.tab.status_lbl.text().startswith("IPs: jointops.exe is not running on this PC")
    assert rig.tab.ban_now("troll", kind="ip") == [] and "cannot IP-ban" in rig.logged[-1]


def test_expired_entries_drop_off_in_housekeeping(tmp_path):
    rig = Rig(tmp_path)
    rig.tab.bans.add(br.BanEntry("name", "Troll", added_at=NOW, expires_at=NOW + 10))
    rig.now = NOW + 11
    rig.tab._housekeeping()
    assert rig.tab.bans.entries == [] and "expired" in rig.logged[-1]
