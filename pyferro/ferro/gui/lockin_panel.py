"""The Lock-in tab: read the 5302's settings and change them from the program.

While monitoring or recording, changes go through the acquisition thread
(``Acquisition.set_lockin``), which sends them between samples and writes what took
effect into the data file. Otherwise the tab opens its own connection, like the Test
button. Either way the settings are read back afterwards and checked against the lab
manual's table.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..acquisition import open_lockin
from ..instruments.lockin5302 import (
    CAPACITANCE_SETUP,
    FILTER_MODES,
    REFERENCE_MODES,
    RESERVE_MODES,
    SENSITIVITY_LABELS,
    SIGNAL_INPUTS,
    TIME_CONSTANT_LABELS,
    check_setup,
    commands_for,
)
from .setup_panel import setup_table_html
from .widgets import run_task


def _combo(items) -> QComboBox:
    box = QComboBox()
    for item in items:
        box.addItem(item, item)
    return box


class LockinPanel(QWidget):
    log = Signal(str, str)

    def __init__(self, get_cfg: Callable, get_acq: Callable, parent=None) -> None:
        super().__init__(parent)
        self._get_cfg, self._get_acq = get_cfg, get_acq
        self._known: dict | None = None  # the settings last read from the instrument

        lay = QVBoxLayout(self)
        box = QGroupBox("EG&G 5302 settings")
        form = QFormLayout(box)
        self.reference = _combo(REFERENCE_MODES.values())
        form.addRow("Reference", self.reference)
        self.osc_khz = QDoubleSpinBox()
        self.osc_khz.setRange(0.000001, 1000.0)
        self.osc_khz.setDecimals(3)
        self.osc_khz.setSuffix(" kHz")
        form.addRow("Oscillator frequency", self.osc_khz)
        self.osc_v = QDoubleSpinBox()
        self.osc_v.setRange(0.005, 5.0)
        self.osc_v.setDecimals(3)
        self.osc_v.setSingleStep(0.1)
        self.osc_v.setSuffix(" V")
        form.addRow("Oscillator level", self.osc_v)
        self.sensitivity = _combo(SENSITIVITY_LABELS)
        form.addRow("Sensitivity", self.sensitivity)
        self.expand = QCheckBox("Expand X ×10")
        form.addRow("Expand", self.expand)
        self.time_constant = _combo(TIME_CONSTANT_LABELS)
        form.addRow("Time constant", self.time_constant)
        self.filter = _combo(FILTER_MODES.values())
        form.addRow("Filter", self.filter)
        self.reserve = _combo(RESERVE_MODES.values())
        form.addRow("Dynamic reserve", self.reserve)
        self.signal_input = _combo(SIGNAL_INPUTS.values())
        form.addRow("Signal input", self.signal_input)
        lay.addWidget(box)

        row = QHBoxLayout()
        self.read_btn = QPushButton("Read from lock-in")
        self.manual_btn = QPushButton("Lab-manual values")
        self.manual_btn.setToolTip("Fill in the lab manual's 5302 table (capacitance measurement). "
                                   "Nothing is sent until Apply.")
        self.apply_btn = QPushButton("Apply to lock-in")
        for b in (self.read_btn, self.manual_btn, self.apply_btn):
            row.addWidget(b)
        lay.addLayout(row)

        self.status = QLabel("Read the lock-in to see its settings.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.checks = QLabel("")
        self.checks.setTextFormat(Qt.RichText)
        self.checks.setWordWrap(True)
        lay.addWidget(self.checks)
        note = QLabel("Front panel only: the coupling and grounding buttons on the preamplifier, "
                      "and the phase tuning (AUTO, then the left PHASE key, with the sample "
                      "disconnected). Changes made here during a run are written into the "
                      "data file, as are front-panel changes, within a minute.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #5f6368;")
        lay.addWidget(note)
        lay.addStretch(1)

        self.read_btn.clicked.connect(self._read)
        self.manual_btn.clicked.connect(self._fill_from_manual)
        self.apply_btn.clicked.connect(self._apply)
        self._tasks = []

    # --- values in the controls ---------------------------------------------------------
    def values(self) -> dict:
        return {
            "signal_input": self.signal_input.currentData(),
            "reference_mode": self.reference.currentData(),
            "oscillator_hz": round(self.osc_khz.value() * 1000.0, 6),
            "oscillator_v": round(self.osc_v.value(), 3),
            "sensitivity_index": self.sensitivity.currentIndex(),
            "expand": self.expand.isChecked(),
            "time_constant_index": self.time_constant.currentIndex(),
            "dynamic_reserve": self.reserve.currentData(),
            "filter": self.filter.currentData(),
        }

    def show_settings(self, s: dict) -> None:
        """Fill the controls from settings read from the lock-in, and check them."""
        if "reference_mode" not in s:
            return  # another model's settings
        self._known = dict(s)
        for combo, key in ((self.reference, "reference_mode"), (self.filter, "filter"),
                           (self.reserve, "dynamic_reserve"), (self.signal_input, "signal_input")):
            if s.get(key) is not None:
                combo.setCurrentIndex(max(0, combo.findData(s[key])))
        if s.get("oscillator_hz") is not None:
            self.osc_khz.setValue(s["oscillator_hz"] / 1000.0)
        if s.get("oscillator_v") is not None:
            self.osc_v.setValue(s["oscillator_v"])
        self.sensitivity.setCurrentIndex(s["sensitivity_index"])
        self.time_constant.setCurrentIndex(s["time_constant_index"])
        self.expand.setChecked(bool(s["expand"]))
        self.checks.setText(setup_table_html(check_setup(s)))

    def _fill_from_manual(self) -> None:
        wanted = {key: value for key, _, value, _, _ in CAPACITANCE_SETUP}
        self.reference.setCurrentIndex(self.reference.findData(wanted["reference_mode"]))
        self.osc_khz.setValue(wanted["oscillator_hz"] / 1000.0)
        self.osc_v.setValue(wanted["oscillator_v"])
        self.sensitivity.setCurrentIndex(SENSITIVITY_LABELS.index(wanted["sensitivity"]))
        self.expand.setChecked(wanted["expand"])
        self.time_constant.setCurrentIndex(TIME_CONSTANT_LABELS.index(wanted["time_constant"][0]))
        self.filter.setCurrentIndex(self.filter.findData(wanted["filter"]))
        self.reserve.setCurrentIndex(self.reserve.findData(wanted["dynamic_reserve"]))
        self.signal_input.setCurrentIndex(self.signal_input.findData(wanted["signal_input"]))
        self.status.setText("Filled in the lab manual's values. Press Apply to send them.")

    def changes(self) -> dict:
        """The controls that differ from what was last read (all of them if nothing was)."""
        wanted = self.values()
        if not self._known:
            return wanted
        out = {}
        for key, value in wanted.items():
            before = self._known.get(key)
            if isinstance(value, float) and before is not None:
                if abs(value - before) <= 1e-4 * max(abs(value), 1e-9):
                    continue
            elif before == value:
                continue
            out[key] = value
        return out

    # --- actions ------------------------------------------------------------------------
    def _usable(self) -> bool:
        model = self._get_cfg().lockin.model
        if model != "5302":
            self.status.setText(f"Setting the lock-in from PyFERRO is available for the 5302; "
                                f"the {model.upper()} is selected on the Instruments tab.")
            return False
        return True

    def _busy(self, busy: bool) -> None:
        for b in (self.read_btn, self.manual_btn, self.apply_btn):
            b.setEnabled(not busy)

    def _read(self) -> None:
        if not self._usable():
            return
        acq = self._get_acq()
        if acq is not None:
            acq.refresh_lockin()  # the result arrives through show_settings
            self.status.setText("Reading at the next sample…")
            return
        cfg = self._get_cfg()

        def work():
            li = open_lockin(cfg)
            try:
                return li.settings()
            finally:
                li.close()

        self._run(work, "Read the lock-in's settings.")

    def _apply(self) -> None:
        if not self._usable():
            return
        changes = self.changes()
        if not changes:
            self.status.setText("Nothing to change: the lock-in already has these settings.")
            return
        try:
            planned = commands_for(changes)
        except ValueError as exc:
            self.status.setText(f"✘ {exc}")
            return
        listing = "\n".join(f"  {what}   ({command})" for command, what in planned)
        acq = self._get_acq()
        when = ("between samples of the running measurement; the change is written into "
                "the data file" if acq is not None else "now")
        if QMessageBox.question(self, "Change lock-in settings",
                                f"Send these to the lock-in {when}?\n\n{listing}") != QMessageBox.Yes:
            return
        if acq is not None:
            acq.set_lockin(changes)
            self.status.setText("Sent at the next sample; the settings read back will show here.")
            return
        cfg = self._get_cfg()

        def work():
            li = open_lockin(cfg)
            try:
                sent = li.apply(changes)
                return {**li.settings(), "_sent": sent}
            finally:
                li.close()

        self._run(work, None)

    def _run(self, work, message: str | None) -> None:
        self._busy(True)
        self.status.setText("Talking to the lock-in…")

        def done(s):
            self._busy(False)
            sent = s.pop("_sent", None)
            self.show_settings(s)
            if sent:
                text = "Lock-in set: " + ", ".join(sent)
                self.log.emit("info", text)
                self.status.setText("✔ " + text + ". Read back below.")
            else:
                self.status.setText("✔ " + (message or "Done."))

        def failed(msg):
            self._busy(False)
            self.status.setText("✘ " + msg)
            self.log.emit("warning", f"Lock-in tab: {msg}")

        self._tasks.append(run_task(work, done, failed))
