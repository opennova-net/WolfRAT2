"""The "Private welcome" box on the Messages tab.

Shows what is in the server's script right now, lets the admin change it, and
says in one sentence what state things are in. All file work is in
`server_wac`; this is only the form.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox,
                             QVBoxLayout)

from wolfrat import server_wac

COLOURS = (
    ("Green", "00ff00"),
    ("Yellow", "ffff00"),
    ("Orange", "ff8800"),
    ("Red", "ff3030"),
    ("Light blue", "40c0ff"),
    ("White", "ffffff"),
    ("The game's own colour", ""),
)


SUGGESTED_SECOND_LINE = "Chat commands: !kd  !switch  !vote mapname  !skip"


class PrivateWelcomePanel(QGroupBox):
    def __init__(self, server_dir: Callable[[], str],
                 log: Optional[Callable[[str], None]] = None, parent=None):
        super().__init__("Private welcome (only the player who joined sees it)", parent)
        self._server_dir = server_dir
        self._log = log or (lambda _text: None)
        self._build()
        self.reload()

    def _build(self):
        layout = QVBoxLayout(self)

        self.enabled_cb = QCheckBox("Whisper a welcome to every player who joins")
        self.enabled_cb.toggled.connect(self._edited)
        layout.addWidget(self.enabled_cb)

        self.text_input = QLineEdit()
        self.text_input.setMaxLength(server_wac.MAX_TEXT)
        self.text_input.setPlaceholderText("Welcome to my server")
        self.text_input.textChanged.connect(self._edited)
        layout.addWidget(self.text_input)

        row = QHBoxLayout()
        row.addWidget(QLabel("Show it"))
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(server_wac.MIN_SECONDS, server_wac.MAX_SECONDS)
        self.seconds_spin.setSuffix(" s after they spawn")
        self.seconds_spin.setValue(server_wac.DEFAULT_SECONDS)
        self.seconds_spin.valueChanged.connect(self._edited)
        row.addWidget(self.seconds_spin)
        row.addWidget(QLabel("in"))
        self.colour_combo = QComboBox()
        for label, value in COLOURS:
            self.colour_combo.addItem(label, value)
        self.colour_combo.currentIndexChanged.connect(self._edited)
        row.addWidget(self.colour_combo)
        row.addStretch()
        layout.addLayout(row)

        self.second_cb = QCheckBox("Then a second line - tell them the chat commands they can use")
        self.second_cb.toggled.connect(self._second_toggled)
        layout.addWidget(self.second_cb)

        self.text2_input = QLineEdit()
        self.text2_input.setMaxLength(server_wac.MAX_TEXT)
        self.text2_input.setPlaceholderText(SUGGESTED_SECOND_LINE)
        self.text2_input.textChanged.connect(self._edited)
        layout.addWidget(self.text2_input)

        row = QHBoxLayout()
        row.addWidget(QLabel("Show it"))
        self.gap_spin = QSpinBox()
        self.gap_spin.setRange(server_wac.MIN_GAP, server_wac.MAX_GAP)
        self.gap_spin.setSuffix(" s after the first line")
        self.gap_spin.setValue(server_wac.DEFAULT_GAP)
        self.gap_spin.valueChanged.connect(self._edited)
        row.addWidget(self.gap_spin)
        row.addWidget(QLabel("in"))
        self.colour2_combo = QComboBox()
        for label, value in COLOURS:
            self.colour2_combo.addItem(label, value)
        self.colour2_combo.setCurrentIndex(self.colour2_combo.findData(server_wac.DEFAULT_COLOUR2))
        self.colour2_combo.currentIndexChanged.connect(self._edited)
        row.addWidget(self.colour2_combo)
        row.addStretch()
        self.save_btn = QPushButton("Save to the server")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        self.state_lbl = QLabel()
        self.state_lbl.setWordWrap(True)
        layout.addWidget(self.state_lbl)

    # ------------------------------------------------------------- state
    def _form(self) -> Optional[server_wac.Welcome]:
        if not self.enabled_cb.isChecked():
            return None
        first = (self.text_input.text().strip(), self.seconds_spin.value(),
                 self.colour_combo.currentData() or "")
        if not self.second_cb.isChecked():
            return server_wac.Welcome(*first)
        return server_wac.Welcome(*first, self.text2_input.text().strip(),
                                  self.colour2_combo.currentData() or "", self.gap_spin.value())

    def _second_toggled(self, on: bool):
        if on and not self.text2_input.text().strip():
            self.text2_input.setText(SUGGESTED_SECOND_LINE)     # a start; edit to suit the server
        self._edited()

    def _on_server(self) -> Optional[server_wac.Welcome]:
        folder = self._server_dir()
        if not folder:
            return None
        try:
            return server_wac.read_welcome(folder)
        except server_wac.ServerWacError:
            return None

    def showEvent(self, event):
        super().showEvent(event)
        if not self.save_btn.isEnabled():      # nothing unsaved in the form
            self.reload()

    def reload(self):
        """Fill the form from what the server's script holds."""
        current = self._on_server()
        widgets = (self.enabled_cb, self.text_input, self.seconds_spin, self.colour_combo,
                   self.second_cb, self.text2_input, self.gap_spin, self.colour2_combo)
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled_cb.setChecked(current is not None)
        if current is not None:
            self.text_input.setText(current.text)
            self.seconds_spin.setValue(current.seconds)
            index = self.colour_combo.findData(current.colour)
            self.colour_combo.setCurrentIndex(index if index >= 0 else 0)
            self.second_cb.setChecked(bool(current.text2))
            if current.text2:
                self.text2_input.setText(current.text2)
                self.gap_spin.setValue(current.gap)
                index = self.colour2_combo.findData(current.colour2)
                self.colour2_combo.setCurrentIndex(index if index >= 0 else 0)
        for widget in widgets:
            widget.blockSignals(False)
        self._edited()

    def _edited(self, *_):
        on = self.enabled_cb.isChecked()
        for widget in (self.text_input, self.seconds_spin, self.colour_combo, self.second_cb):
            widget.setEnabled(on)
        for widget in (self.text2_input, self.gap_spin, self.colour2_combo):
            widget.setEnabled(on and self.second_cb.isChecked())
        folder = self._server_dir()
        if not folder:
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(
                "WolfRAT cannot see a game server on this PC yet. The private welcome is written "
                "into the server's folder, so WolfRAT has to run on the same PC as the server.")
            return
        wanted, current = self._form(), self._on_server()
        problem = server_wac.text_problem(wanted.text) if wanted else None
        if wanted and not problem and self.second_cb.isChecked():
            problem = server_wac.text_problem(wanted.text2)
            if problem:
                problem = ("Type the second line, or untick it." if not wanted.text2
                           else f"Second line: {problem}")
        if problem:
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(problem)
        elif wanted == current:
            self.save_btn.setEnabled(False)
            self.state_lbl.setText(
                "Saved on the server. New players see it a few seconds after they spawn "
                "(once on each map)." if current else "Off. Nothing is whispered to new players.")
        else:
            self.save_btn.setEnabled(True)
            self.state_lbl.setText("Not saved yet - press 'Save to the server'.")

    # ------------------------------------------------------------- action
    def save(self):
        folder = self._server_dir()
        if not folder:
            self._edited()
            return
        wanted = self._form()
        try:
            if wanted is None:
                server_wac.remove_welcome(folder)
                self._log("Private welcome switched off (from the next map change).")
            else:
                server_wac.write_welcome(folder, wanted)
                self._log(f"Private welcome saved: {wanted.text}"
                          + (f"  /  {wanted.text2}" if wanted.text2 else ""))
        except server_wac.ServerWacError as exc:
            self.state_lbl.setText(str(exc))
            self._log(str(exc))
            return
        self._edited()
        self.state_lbl.setText(self.state_lbl.text().split(" New players")[0]
                               + " It takes effect at the next map change - the game only reads "
                                 "its scripts when a map loads.")
