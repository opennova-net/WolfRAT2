"""The "Dynamic" page of the Weather tab: every widget maps onto DynamicConfig."""

from __future__ import annotations

from typing import Callable, Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QFrame, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from wolfrat import weather_dynamic as dyn

_HELP = (
    "Weather that arrives on its own. Between clear spells a <b>front</b> rolls "
    "in - the sky clouds over, it builds to its peak, holds, then eases off the "
    "same way. Every front is a different strength, and the fronts keep their own "
    "clock: if a storm has five minutes left when the map changes, the next map "
    "opens with five minutes of storm."
    "<br><br>Pressing a button on the Manual page, or a mod's <b>!storm</b>, always "
    "wins - dynamic weather waits until that is over. <b>Clear the weather</b> "
    "ends the current front early."
)


def _spin(low, high, value, suffix="", width=78):
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setValue(max(low, min(high, int(value))))
    if suffix:
        spin.setSuffix(suffix)
    spin.setMaximumWidth(width)
    spin.setMinimumHeight(26)
    return spin


def _note(text=""):
    """A wrapped hint that always has room for two lines."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("font-size: 9pt; color: #a89830;")
    label.setMinimumWidth(240)
    label.setMinimumHeight(label.fontMetrics().lineSpacing() * 2 + 6)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
    return label


class DynamicPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, config: dyn.DynamicConfig, button_cls: type = QPushButton,
                 map_list: Callable[[], Iterable[tuple[str, str]]] | None = None):
        super().__init__()
        self._button_cls = button_cls
        self._map_list = map_list or (lambda: ())
        self._map_rules = dict(config.map_rules)
        self._seen = {}            # map file -> "terrain, climate" learned as maps get played
        self._loading = True
        self._build(config)
        self._loading = False
        self.refresh_maps()

    # ------------------------------------------------------------------ UI
    def _build(self, config: dyn.DynamicConfig):
        root = QHBoxLayout(self)
        # The left column is taller than a small window, so it scrolls.
        left_host = QWidget()
        left = QVBoxLayout(left_host)
        left.setContentsMargins(0, 0, 6, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left_host)
        right = QVBoxLayout()
        root.addWidget(scroll, 3)
        root.addLayout(right, 2)

        # -- help first: the column scrolls, so it costs nothing
        help_group = QGroupBox("How it works")
        help_box = QVBoxLayout()
        help_lbl = QLabel(_HELP)
        help_lbl.setWordWrap(True)
        help_lbl.setTextFormat(Qt.TextFormat.RichText)
        help_lbl.setStyleSheet("font-size: 9pt; color: #c8b040;")
        help_lbl.setMinimumWidth(240)
        help_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        help_box.addWidget(help_lbl)
        help_group.setLayout(help_box)
        left.addWidget(help_group)

        # -- master
        main_group = QGroupBox("Dynamic weather")
        main = QVBoxLayout()
        top = QHBoxLayout()
        self.enabled_cb = QCheckBox("Let the weather change on its own")
        self.enabled_cb.setChecked(config.enabled)
        top.addWidget(self.enabled_cb)
        top.addStretch()
        top.addWidget(QLabel("How often:"))
        self.frequency_combo = QComboBox()
        for key, label in dyn.FREQUENCY_LABELS.items():
            self.frequency_combo.addItem(label, key)
        self.frequency_combo.setCurrentIndex(max(0, self.frequency_combo.findData(config.frequency)))
        top.addWidget(self.frequency_combo)
        main.addLayout(top)

        self.status_lbl = QLabel("Dynamic weather is off.")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("font-size: 10pt; color: #c8b040; padding: 4px 0;")
        self.status_lbl.setMinimumWidth(240)
        self.status_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        main.addWidget(self.status_lbl)

        spans = QGridLayout()
        self.clear_min = _spin(0, 600, config.clear_min, " min")
        self.clear_max = _spin(0, 600, config.clear_max, " min")
        self.hold_min = _spin(1, 240, config.hold_min, " min")
        self.hold_max = _spin(1, 240, config.hold_max, " min")
        self.fade_min = _spin(10, 600, config.fade_min, " s")
        self.fade_max = _spin(10, 600, config.fade_max, " s")
        self._span_label = _note()
        spans.addWidget(QLabel("Clear spells last"), 0, 0)
        spans.addWidget(self.clear_min, 0, 1)
        spans.addWidget(QLabel("to"), 0, 2)
        spans.addWidget(self.clear_max, 0, 3)
        spans.addWidget(QLabel("A front holds its peak"), 1, 0)
        spans.addWidget(self.hold_min, 1, 1)
        spans.addWidget(QLabel("to"), 1, 2)
        spans.addWidget(self.hold_max, 1, 3)
        spans.addWidget(QLabel("Each change fades over"), 2, 0)
        spans.addWidget(self.fade_min, 2, 1)
        spans.addWidget(QLabel("to"), 2, 2)
        spans.addWidget(self.fade_max, 2, 3)
        spans.addWidget(self._span_label, 3, 0, 1, 5)
        spans.setColumnStretch(4, 1)
        main.addLayout(spans)
        main_group.setLayout(main)
        left.addWidget(main_group)

        # -- types
        types_group = QGroupBox("What can roll in  (bigger number = more often)")
        types = QGridLayout()
        self._type_widgets = {}
        for index, name in enumerate(dyn.TYPES):
            weight = int(config.weights.get(name, 0))
            box = QCheckBox(dyn.TYPE_LABELS[name])
            box.setChecked(weight > 0)
            tip = "Builds up through: " + " -> ".join(dyn.TYPE_LABELS[n] for n in dyn.LADDERS[name])
            if name in ("snow", "blizzard"):
                tip += ("\nOn a map that is not a snow map (Auto or Rain) this falls as "
                        + ("rain" if name == "snow" else "a storm") + " instead.")
            elif name in ("drizzle", "rain", "storm"):
                tip += "\nOn a snow map this falls as snow instead."
            box.setToolTip(tip)
            spin = _spin(1, 100, weight or 10, width=64)
            spin.setEnabled(weight > 0)
            box.toggled.connect(spin.setEnabled)
            row, column = divmod(index, 2)
            types.addWidget(box, row, column * 3)
            types.addWidget(spin, row, column * 3 + 1)
            self._type_widgets[name] = (box, spin)
        types.setColumnStretch(2, 1)
        types.setColumnStretch(5, 1)
        self.mix_lbl = _note()
        types.addWidget(self.mix_lbl, 4, 0, 1, 6)
        types_group.setLayout(types)
        left.addWidget(types_group)

        # -- extras
        extras_group = QGroupBox("Fog, earthquakes and chat")
        extras = QGridLayout()
        self.fog_min = _spin(20, 2000, config.fog_min, " m")
        self.fog_max = _spin(20, 2000, config.fog_max, " m")
        extras.addWidget(QLabel("Foggy fronts pick a fog setting from"), 0, 0)
        extras.addWidget(self.fog_min, 0, 1)
        extras.addWidget(QLabel("to"), 0, 2)
        extras.addWidget(self.fog_max, 0, 3)
        fog_note = _note("Players see further than the number: at 50 you can still make things "
                         "out at 150-200 m. Every front rolls its own.")
        extras.addWidget(fog_note, 1, 0, 1, 5)

        self.quake_cb = QCheckBox("Rare earthquakes, about one every")
        self.quake_cb.setChecked(config.quake_enabled)
        self.quake_cb.setToolTip("They really shake the screen and nudge players and vehicles.")
        self.quake_every = _spin(5, 1440, config.quake_every, " min")
        self.quake_max = _spin(2, 40, config.quake_max_seconds, " s")
        extras.addWidget(self.quake_cb, 2, 0)
        extras.addWidget(self.quake_every, 2, 1)
        extras.addWidget(QLabel("up to"), 2, 2)
        extras.addWidget(self.quake_max, 2, 3)
        for widget in (self.quake_every, self.quake_max):
            widget.setEnabled(config.quake_enabled)
            self.quake_cb.toggled.connect(widget.setEnabled)

        self.announce_cb = QCheckBox("Tell players in chat when a front moves in")
        self.announce_cb.setChecked(config.announce)
        extras.addWidget(self.announce_cb, 3, 0, 1, 4)
        self.empty_cb = QCheckBox("Pause while nobody is on the server")
        self.empty_cb.setChecked(config.pause_when_empty)
        extras.addWidget(self.empty_cb, 4, 0, 1, 4)
        self.lightning_cb = QCheckBox("Lightning and thunder in storms")
        self.lightning_cb.setChecked(config.lightning)
        extras.addWidget(self.lightning_cb, 5, 0, 1, 4)
        self.lightning_lbl = _note()
        extras.addWidget(self.lightning_lbl, 6, 0, 1, 5)
        extras.setColumnStretch(4, 1)
        extras_group.setLayout(extras)
        left.addWidget(extras_group)
        left.addStretch()

        # -- right: maps
        maps_group = QGroupBox("Rain or snow, map by map")
        maps = QVBoxLayout()
        search_row = QHBoxLayout()
        self.map_search = QLineEdit()
        self.map_search.setPlaceholderText("Find a map, or type a name - e.g. cod kill house")
        self.map_search.setClearButtonEnabled(True)
        self.map_search.textChanged.connect(self._fill_maps)
        search_row.addWidget(self.map_search)
        self.only_rules_cb = QCheckBox("only changed")
        self.only_rules_cb.toggled.connect(self._fill_maps)
        search_row.addWidget(self.only_rules_cb)
        maps.addLayout(search_row)

        self.this_map_lbl = _note("Auto: WolfRAT reads each map as it loads and picks rain or snow.")
        maps.addWidget(self.this_map_lbl)

        self.map_table = QTableWidget(0, 3)
        self.map_table.setHorizontalHeaderLabels(["Map", "Seen as", "Weather"])
        self.map_table.verticalHeader().setVisible(False)
        self.map_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.map_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        header = self.map_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.map_table.setMinimumHeight(220)
        maps.addWidget(self.map_table, 1)

        maps.addWidget(QLabel("Set the selected maps to:"))
        rule_grid = QGridLayout()
        for index, rule in enumerate((dyn.MAP_AUTO, dyn.MAP_NO_SNOW, dyn.MAP_SNOW, dyn.MAP_NONE)):
            button = self._button_cls(dyn.MAP_RULE_LABELS[rule])
            button.clicked.connect(lambda _=False, r=rule: self._set_rule(r))
            rule_grid.addWidget(button, index // 2, index % 2)
        maps.addLayout(rule_grid)
        self.map_hint = _note()
        snow_row = QHBoxLayout()
        snow_row.addWidget(QLabel("Snow terrains:"))
        self.snow_edit = QLineEdit(", ".join(config.snow_terrains))
        self.snow_edit.setToolTip(
            "Auto treats a map as snow when the map itself says Snow, or when its terrain is "
            "in this list (mappers often leave a snow map set to Desert). Comma separated."
        )
        self.snow_edit.editingFinished.connect(self._emit)
        snow_row.addWidget(self.snow_edit)
        maps.addWidget(self.map_hint)
        maps.addLayout(snow_row)
        maps_group.setLayout(maps)
        right.addWidget(maps_group, 1)

        # -- wiring
        for box in (self.enabled_cb, self.quake_cb, self.announce_cb, self.empty_cb, self.lightning_cb):
            box.toggled.connect(self._emit)
        for spin in (self.clear_min, self.clear_max, self.hold_min, self.hold_max, self.fade_min,
                     self.fade_max, self.fog_min, self.fog_max, self.quake_every, self.quake_max):
            spin.valueChanged.connect(self._emit)
        for box, spin in self._type_widgets.values():
            box.toggled.connect(self._emit)
            spin.valueChanged.connect(self._emit)
        self.frequency_combo.currentIndexChanged.connect(self._emit)
        self._refresh_labels()

    # ------------------------------------------------------------ config
    def config(self) -> dyn.DynamicConfig:
        return dyn.DynamicConfig(
            enabled=self.enabled_cb.isChecked(),
            frequency=self.frequency_combo.currentData() or "normal",
            clear_min=self.clear_min.value(), clear_max=self.clear_max.value(),
            hold_min=self.hold_min.value(), hold_max=self.hold_max.value(),
            fade_min=self.fade_min.value(), fade_max=self.fade_max.value(),
            weights={name: (spin.value() if box.isChecked() else 0)
                     for name, (box, spin) in self._type_widgets.items()},
            fog_min=self.fog_min.value(), fog_max=self.fog_max.value(),
            quake_enabled=self.quake_cb.isChecked(), quake_every=self.quake_every.value(),
            quake_max_seconds=self.quake_max.value(), announce=self.announce_cb.isChecked(),
            pause_when_empty=self.empty_cb.isChecked(), lightning=self.lightning_cb.isChecked(),
            map_rules=dict(self._map_rules),
            snow_terrains=[dyn.terrain_key(t) for t in self.snow_edit.text().split(",") if t.strip()],
        )

    def _emit(self, *_):
        if self._loading:
            return
        self._refresh_labels()
        self.changed.emit()

    def _refresh_labels(self):
        config = self.config()
        custom = config.frequency == "custom"
        for spin in (self.clear_min, self.clear_max, self.hold_min, self.hold_max):
            spin.setEnabled(custom)
        if custom:
            self._span_label.setText("")
        else:
            clear_min, clear_max, hold_min, hold_max = config.spans()
            self._span_label.setText(
                f"{dyn.FREQUENCY_LABELS[config.frequency]} = clear spells {clear_min}-{clear_max} min, "
                f"peaks {hold_min}-{hold_max} min. Pick Custom to set your own."
            )
        total = sum(config.weights.values())
        if total <= 0:
            self.mix_lbl.setText("Nothing is ticked, so the sky will stay clear.")
        else:
            parts = [f"{dyn.TYPE_LABELS[name].lower()} {round(100 * weight / total)}%"
                     for name, weight in config.weights.items() if weight > 0]
            self.mix_lbl.setText("Out of 100 fronts, roughly: " + ", ".join(parts) + ".")

    def set_status(self, text: str):
        self.status_lbl.setText(text)

    def set_lightning_text(self, text: str):
        self.lightning_lbl.setText(text)

    def set_lightning_status(self, available: bool | None):
        if available is None:
            self.lightning_lbl.setText("")
        elif available:
            self.lightning_lbl.setText("Ready - this server has the lightning add-on.")
        else:
            self.lightning_lbl.setText(
                "Not on this server yet - lightning needs a small server add-on that is "
                "still being finished. Storms run without flashes until then."
            )

    # -------------------------------------------------------------- maps
    def seen(self) -> dict:
        return dict(self._seen)

    def load_seen(self, seen):
        if isinstance(seen, dict):
            self._seen = {str(k): str(v) for k, v in seen.items()}

    def show_current_map(self, map_name, info, snow: bool, rule: str):
        """Explain what Auto made of the map that is loaded right now."""
        verdict = "snow" if snow else "rain"
        if rule == dyn.MAP_AUTO:
            why = f"Auto picked {verdict}"
        elif rule == dyn.MAP_NONE:
            why = "set by hand: no weather"
        else:
            why = f"set by hand: {dyn.MAP_RULE_LABELS[rule].lower()} (Auto would pick {verdict})"
        self.this_map_lbl.setText(f"Now: {info.title or map_name or '?'}  -  {info.describe()}.  {why}.")
        key = dyn.map_key(map_name or "")
        label = f"{info.terrain}, {'snow' if snow else 'not snow'}"
        if key and info.terrain and self._seen.get(key) != label:
            self._seen[key] = label
            self._fill_maps()
            return True
        return False

    def refresh_maps(self):
        self._maps = [(str(f), str(n or "")) for f, n in self._map_list() if f]
        self._fill_maps()

    def _fill_maps(self, *_):
        needle = self.map_search.text().strip()
        key = dyn.map_key(needle)
        only = self.only_rules_cb.isChecked()
        real_keys = {dyn.map_key(f) for f, _ in self._maps}
        rows = []
        for typed, rule in sorted(self._map_rules.items()):
            if dyn.map_key(typed) not in real_keys:
                rows.append((typed, f"{typed}   (typed name)", rule))
        for file_name, title in self._maps:
            rule = dyn.map_rule_for(file_name, self._map_rules)
            label = file_name if not title or title == file_name else f"{file_name}   -   {title}"
            rows.append((file_name, label, rule))
        shown = []
        for name, label, rule in rows:
            if only and rule == dyn.MAP_NORMAL:
                continue
            if key and key not in dyn.map_key(label) and dyn.map_rule_for(name, {needle: "x"}) != "x":
                continue
            shown.append((name, label, rule))

        self.map_table.setRowCount(len(shown))
        for row, (name, label, rule) in enumerate(shown):
            item = QTableWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.map_table.setItem(row, 0, item)
            self.map_table.setItem(row, 1, QTableWidgetItem(self._seen.get(dyn.map_key(name), "")))
            self.map_table.setItem(row, 2, QTableWidgetItem(dyn.MAP_RULE_LABELS[rule]))
        changed = sum(1 for _ in self._map_rules)
        if needle and not shown:
            self.map_hint.setText(
                f"No map matches yet. Press a button to keep a rule for \"{needle}\" - it will "
                "match the map by name when it comes up."
            )
        elif not self._maps:
            self.map_hint.setText(
                "The map list fills in once WolfRAT has fetched it from the server (Missions tab). "
                "Until then, type a map name and press a button."
            )
        else:
            self.map_hint.setText(f"{changed} map(s) set by hand. Everything else is on Auto.")

    def _set_rule(self, rule: str):
        names = [self.map_table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                 for index in self.map_table.selectionModel().selectedRows()]
        typed = self.map_search.text().strip()
        if not names and typed and self.map_table.rowCount() == 0:
            names = [typed]
        if not names:
            self.map_hint.setText("Select one or more maps first (or type a name that is not in the list).")
            return
        for name in names:
            # drop any older rule that was steering this map, then set the new one
            for existing in [k for k in self._map_rules
                             if dyn.map_rule_for(name, {k: "x"}) == "x"]:
                del self._map_rules[existing]
            if rule != dyn.MAP_NORMAL:
                self._map_rules[name] = rule
        self._fill_maps()
        self._emit()
