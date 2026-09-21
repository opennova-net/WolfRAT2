"""Private welcome box: real widgets against a temp server folder."""
import os
import sys

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from wolfrat import server_wac as sw
from wolfrat import weather as w
from wolfrat.private_welcome_panel import PrivateWelcomePanel

_app = QApplication.instance() or QApplication(sys.argv[:1])


def test_no_server_on_this_pc_says_so_and_cannot_save():
    panel = PrivateWelcomePanel(lambda: "")
    panel.enabled_cb.setChecked(True)
    panel.text_input.setText("Hello")
    assert not panel.save_btn.isEnabled()
    assert "same PC" in panel.state_lbl.text()


def test_type_save_reload_switch_off(tmp_path):
    w.install_addon(tmp_path)
    logged = []
    panel = PrivateWelcomePanel(lambda: str(tmp_path), log=logged.append)
    assert not panel.enabled_cb.isChecked() and "Off" in panel.state_lbl.text()

    panel.enabled_cb.setChecked(True)
    assert "Type the welcome" in panel.state_lbl.text() and not panel.save_btn.isEnabled()
    panel.text_input.setText("Welcome to Badger's server")
    panel.seconds_spin.setValue(15)
    panel.colour_combo.setCurrentIndex(panel.colour_combo.findData("ffff00"))
    assert "Not saved yet" in panel.state_lbl.text() and panel.save_btn.isEnabled()

    panel.save_btn.click()
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server", 15, "ffff00")
    assert "next map change" in panel.state_lbl.text() and not panel.save_btn.isEnabled()
    assert w.addon_installed(tmp_path) and logged

    fresh = PrivateWelcomePanel(lambda: str(tmp_path))          # WolfRAT restarted
    assert fresh.enabled_cb.isChecked() and fresh.text_input.text() == "Welcome to Badger's server"
    assert fresh.seconds_spin.value() == 15 and fresh.colour_combo.currentData() == "ffff00"
    assert "Saved on the server" in fresh.state_lbl.text()

    fresh.enabled_cb.setChecked(False)
    fresh.save_btn.click()
    assert sw.read_welcome(tmp_path) is None and w.addon_installed(tmp_path)


def test_a_double_quote_is_caught_in_the_form_not_in_the_game(tmp_path):
    panel = PrivateWelcomePanel(lambda: str(tmp_path))
    panel.enabled_cb.setChecked(True)
    panel.text_input.setText('Welcome to "Badger" server')
    assert "double quote" in panel.state_lbl.text() and not panel.save_btn.isEnabled()
    assert not os.path.exists(os.path.join(str(tmp_path), sw.FILENAME))


def test_server_found_later_fills_the_form_when_the_tab_is_shown(tmp_path):
    sw.write_welcome(tmp_path, sw.Welcome("Hi there"))
    folder = {"path": ""}
    panel = PrivateWelcomePanel(lambda: folder["path"])
    assert not panel.enabled_cb.isChecked()
    folder["path"] = str(tmp_path)
    panel.show()
    _app.processEvents()
    assert panel.enabled_cb.isChecked() and panel.text_input.text() == "Hi there"
    panel.hide()


def test_write_failure_is_shown_in_plain_words(tmp_path):
    missing = tmp_path / "gone" / "deeper"
    panel = PrivateWelcomePanel(lambda: str(missing))
    panel.enabled_cb.setChecked(True)
    panel.text_input.setText("Hello")
    panel.save_btn.click()
    assert "administrator" in panel.state_lbl.text()


def test_second_line_tick_suggests_the_commands_saves_and_reloads(tmp_path):
    panel = PrivateWelcomePanel(lambda: str(tmp_path))
    panel.enabled_cb.setChecked(True)
    panel.text_input.setText("Welcome to Badger's server")
    assert not panel.text2_input.isEnabled()
    panel.second_cb.setChecked(True)
    assert "!kd" in panel.text2_input.text() and panel.text2_input.isEnabled()
    panel.gap_spin.setValue(4)
    panel.colour2_combo.setCurrentIndex(panel.colour2_combo.findData("ff8800"))
    panel.save_btn.click()
    saved = sw.read_welcome(tmp_path)
    assert saved.text2 == panel.text2_input.text() and saved.gap == 4 and saved.colour2 == "ff8800"

    fresh = PrivateWelcomePanel(lambda: str(tmp_path))
    assert fresh.second_cb.isChecked() and fresh.gap_spin.value() == 4
    assert fresh.colour2_combo.currentData() == "ff8800" and not fresh.save_btn.isEnabled()

    fresh.text2_input.setText("")
    assert "second line" in fresh.state_lbl.text() and not fresh.save_btn.isEnabled()
    fresh.second_cb.setChecked(False)
    fresh.save_btn.click()
    assert sw.read_welcome(tmp_path) == sw.Welcome("Welcome to Badger's server")
