"""Moderator entrance box: real widgets, fake chat + lightning."""
import json
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from wolfrat.mod_entrance_panel import SETTINGS_FILE, ModEntrancePanel

_app = QApplication.instance() or QApplication(sys.argv[:1])
MODS = ["BadgerLove"]


class Rig:
    def __init__(self, folder, flash_ok=True):
        self.now = 5_000_000.0
        self.said, self.flashes, self.logged = [], 0, []
        self.flash_ok = flash_ok
        self.folder = str(folder)
        self.panel = self.build()

    def build(self):
        return ModEntrancePanel(self.folder, announce=self.said.append, flash=self._flash,
                                log=self.logged.append, clock=lambda: self.now)

    def _flash(self):
        self.flashes += 1
        return self.flash_ok

    def play(self, names, seconds, step=5):
        for _ in range(0, seconds, step):
            self.now += step
            self.panel.on_players([{"name": n} for n in names], MODS)


def test_off_until_ticked_then_announces_with_lightning_and_saves(tmp_path):
    rig = Rig(tmp_path)
    rig.play([], 5)
    rig.play(["BadgerLove"], 60)
    assert rig.said == [] and rig.flashes == 0
    rig.play([], 30)

    rig.panel.enabled_cb.setChecked(True)
    rig.play(["BadgerLove"], 60)
    assert len(rig.said) == 1 and "BadgerLove" in rig.said[0] and rig.flashes == 1
    assert "announced + lightning" in rig.panel.status_lbl.text()

    saved = json.load(open(os.path.join(rig.folder, SETTINGS_FILE), encoding="utf-8"))
    assert saved["enabled"] is True and "badgerlove" in saved["last_announced"]


def test_cooldown_is_remembered_by_a_new_panel(tmp_path):
    rig = Rig(tmp_path)
    rig.panel.enabled_cb.setChecked(True)
    rig.play([], 5)
    rig.play(["BadgerLove"], 60)
    assert len(rig.said) == 1
    rig.panel = rig.build()                       # WolfRAT restarted
    assert rig.panel.enabled_cb.isChecked()
    rig.play([], 5)
    rig.play(["BadgerLove"], 60)
    assert len(rig.said) == 1


def test_lightning_unticked_and_lightning_not_ready(tmp_path):
    rig = Rig(tmp_path, flash_ok=False)
    rig.panel.enabled_cb.setChecked(True)
    rig.play([], 5)
    rig.play(["BadgerLove"], 60)
    assert len(rig.said) == 1 and "no lightning" in rig.panel.status_lbl.text()

    rig2 = Rig(tmp_path / "b")
    os.makedirs(rig2.folder, exist_ok=True)
    rig2.panel.enabled_cb.setChecked(True)
    rig2.panel.lightning_cb.setChecked(False)
    rig2.play([], 5)
    rig2.play(["BadgerLove"], 60)
    assert len(rig2.said) == 1 and rig2.flashes == 0


def test_map_change_does_not_announce_again(tmp_path):
    rig = Rig(tmp_path)
    rig.panel.enabled_cb.setChecked(True)
    rig.panel.cooldown_spin.setValue(0)
    rig.play([], 5)
    rig.play(["BadgerLove", "Rookie"], 60)
    rig.panel.on_disconnected()
    rig.play([], 60)
    rig.play(["Rookie"], 10)
    rig.play(["Rookie", "BadgerLove"], 120)
    assert len(rig.said) == 1


def test_a_failing_chat_send_never_escapes(tmp_path):
    rig = Rig(tmp_path)

    def boom(_text):
        raise RuntimeError("socket closed")
    rig.panel._announce = boom
    rig.panel.enabled_cb.setChecked(True)
    rig.play([], 5)
    rig.play(["BadgerLove"], 60)
    assert rig.flashes == 0 and "could not send" in rig.panel.status_lbl.text()


def test_try_it_now_works_while_switched_off(tmp_path):
    rig = Rig(tmp_path)
    rig.panel.try_btn.click()
    assert len(rig.said) == 1 and rig.flashes == 1
