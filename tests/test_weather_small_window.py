"""1024x768 servers: every weather column scrolls instead of squeezing."""
from PyQt6.QtWidgets import QScrollArea

from tests.test_weather import FakeServer
from tests.test_weather_dynamic_tab import make


def _columns(page):
    return [a for a in page.findChildren(QScrollArea) if a.widgetResizable()]


def test_manual_and_dynamic_pages_have_two_scrolling_columns_each(qtbot, tmp_path):
    tab, _said, _attaches = make(qtbot, tmp_path, FakeServer())
    assert len(_columns(tab.pages.widget(0))) == 2
    assert len(_columns(tab.dynamic_page)) == 2


def test_short_window_scrolls_and_preset_buttons_keep_their_height(qtbot, tmp_path):
    tab, _said, _attaches = make(qtbot, tmp_path, FakeServer())
    tab.resize(960, 420)
    tab.show()
    qtbot.wait(50)
    left = _columns(tab.pages.widget(0))[0]
    assert left.verticalScrollBar().maximum() > 0            # there is something to scroll to
    presets = tab._action_widgets[:7]
    assert all(b.height() >= b.sizeHint().height() for b in presets)
    rows = sorted({b.y() for b in presets})
    assert len(rows) == 2 and rows[1] - rows[0] >= presets[0].height()   # rows do not overlap

    tab.resize(1600, 1400)
    qtbot.wait(50)
    assert left.verticalScrollBar().maximum() == 0           # roomy window: no scroll bar
    tab.hide()


def test_the_mod_switch_sits_above_the_help_text(qtbot, tmp_path):
    tab, _said, _attaches = make(qtbot, tmp_path, FakeServer())
    tab.resize(1024, 600)
    tab.show()
    qtbot.wait(50)
    right = _columns(tab.pages.widget(0))[1]
    assert tab.mods_cb.mapTo(right.widget(), tab.mods_cb.rect().topLeft()).y() < 120
    tab.hide()
