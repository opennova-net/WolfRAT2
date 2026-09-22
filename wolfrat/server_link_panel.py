"""The Server tab's 'Game server on this PC' box: a checklist and one button."""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
)

from wolfrat import server_link as sl

LINK_TEXT = "⚡  Link WolfRAT to the server"
LINKED_TEXT = "✅  Linked  -  server features ready"

_MARKS = {
    sl.OK: ("✔", "#50d860"),
    sl.WAIT: ("…", "#e8c840"),
    sl.NO: ("✖", "#ff6040"),
    sl.OFF: ("○", "#4a4a20"),
}

_LINK_STYLE = """
QPushButton {
    background-color: #0f3a14;
    color: #8cff98;
    border-top: 2px solid #50d860;
    border-left: 2px solid #50d860;
    border-bottom: 2px solid #06200a;
    border-right: 2px solid #06200a;
    border-radius: 5px;
    font-size: 12pt;
    font-weight: bold;
    padding: 9px 26px 7px 26px;
}
QPushButton:hover {
    background-color: #16501c;
    border-top: 2px solid #7dff8a;
    border-left: 2px solid #7dff8a;
}
QPushButton:pressed {
    background-color: #082a0c;
    border-top: 2px solid #06200a;
    border-left: 2px solid #06200a;
    border-bottom: 2px solid #50d860;
    border-right: 2px solid #50d860;
    padding: 7px 26px 9px 26px;
}
QPushButton:disabled {
    background-color: #0a0a00;
    color: #3a3a18;
    border: 2px solid #1a1a08;
}
"""

_BADGE_STYLE = (
    "background-color: #0c2e10; color: #7dff8a; border: 2px solid #3cc850; "
    "border-radius: 6px; font-size: 12pt; font-weight: bold; padding: 8px 22px;"
)


class ServerLinkPanel(QGroupBox):
    """``state()`` -> LinkState; ``link()`` -> (ok, message); ``recheck()`` looks again."""

    def __init__(
        self,
        state: Callable[[], sl.LinkState],
        link: Callable[[], tuple],
        recheck: Callable[[], None],
        log: Optional[Callable[[str], None]] = None,
        button_cls: type = QPushButton,
    ):
        super().__init__("Game server on this PC")
        self._state = state
        self._link = link
        self._recheck = recheck
        self._log = log or (lambda _text: None)
        self._message = ""          # result of the last click, until the state moves on
        self._message_ok = True
        self._last_mode = None

        box = QVBoxLayout(self)
        box.setSpacing(6)

        self.intro_lbl = QLabel(sl.INTRO)
        self.intro_lbl.setWordWrap(True)
        self.intro_lbl.setStyleSheet("color: #a89830; font-size: 9pt;")
        box.addWidget(self.intro_lbl)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._rows = []
        for i in range(3):
            mark = QLabel()
            mark.setFixedWidth(20)
            mark.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            title = QLabel()
            detail = QLabel()
            detail.setWordWrap(True)
            detail.setStyleSheet("color: #a89830; font-size: 9pt;")
            detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text = QVBoxLayout()
            text.setSpacing(1)
            text.addWidget(title)
            text.addWidget(detail)
            grid.addWidget(mark, i, 0)
            grid.addLayout(text, i, 1)
            self._rows.append((mark, title, detail))
        box.addLayout(grid)
        box.addStretch()

        action = QHBoxLayout()
        self.link_btn = button_cls(LINK_TEXT)
        self.link_btn.setObjectName("linkBtn")
        self.link_btn.setStyleSheet(_LINK_STYLE)
        self.link_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link_btn.setToolTip(
            "Checks the game server on this PC and adds WolfRAT's small script to server.wac "
            "in its folder. Safe to press again at any time."
        )
        self.link_btn.clicked.connect(self._on_link)
        action.addWidget(self.link_btn, 1)
        self.badge_lbl = QLabel(LINKED_TEXT)
        self.badge_lbl.setStyleSheet(_BADGE_STYLE)
        self.badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action.addWidget(self.badge_lbl, 1)
        box.addLayout(action)

        self.hint_lbl = QLabel()
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.hint_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        box.addWidget(self.hint_lbl)

        again = QHBoxLayout()
        again.addStretch()
        self.recheck_btn = QPushButton("Check again")
        self.recheck_btn.setToolTip("Look for the game server again right now.")
        self.recheck_btn.setStyleSheet("font-size: 9pt; padding: 2px 10px; min-height: 16px;")
        self.recheck_btn.clicked.connect(self._on_recheck)
        again.addWidget(self.recheck_btn)
        box.addLayout(again)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self):
        try:
            state = self._state()
        except Exception as exc:                  # never let the Server tab die over it
            state = sl.LinkState(looked=True, found=True, problem=f"Could not check: {exc}")
        view = sl.describe(state)
        for (mark, title, detail), row in zip(self._rows, view.rows):
            symbol, colour = _MARKS[row.mark]
            mark.setText(symbol)
            mark.setStyleSheet(f"color: {colour}; font-weight: bold; font-size: 11pt;")
            title.setText(row.title)
            dim = row.mark == sl.OFF
            title.setStyleSheet(
                f"color: {'#4a4a20' if dim else '#e8c840'}; font-weight: bold;")
            detail.setText(row.detail)

        if view.mode != self._last_mode and self._last_mode is not None:
            self._message = ""                   # the world moved on: drop the old click result
        self._last_mode = view.mode

        linked = view.mode == "linked"
        self.link_btn.setVisible(not linked)
        self.link_btn.setEnabled(view.mode == "link")
        self.badge_lbl.setVisible(linked)

        text, ok = (self._message, self._message_ok) if self._message else (view.hint, True)
        self.hint_lbl.setText(text)
        self.hint_lbl.setVisible(bool(text))
        self.hint_lbl.setStyleSheet(
            f"font-size: 9pt; color: {'#a89830' if ok else '#ff8060'};")

    def _on_link(self):
        try:
            ok, message = self._link()
        except Exception as exc:
            ok, message = False, f"Linking failed: {exc}"
        self._log(message)
        # Success speaks through the badge and the checklist; a failure stays
        # under the button until something changes, so the admin can read it.
        self._message, self._message_ok = ("", True) if ok else (message, False)
        self._last_mode = None                   # keep the message through this refresh
        self.refresh()

    def _on_recheck(self):
        self._message = ""
        try:
            self._recheck()
        finally:
            self.refresh()
