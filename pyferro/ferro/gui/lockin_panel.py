"""The Lock-in tab: read the lock-in's settings and change them from the program.

The controls are built from the selected model's driver (``PANEL``, ``SETUP_TABLE``,
``commands``), so the SR830 and the 5302 share this code.

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

from ..acquisition import LOCKIN_MODELS, open_lockin
from ..instruments.lockin5302 import check_setup
from .setup_panel import setup_table_html
from .widgets import run_task


def _combo(items) -> QComboBox:
    box = QComboBox()
    for item in items:
        box.addItem(item, item)
    return box


def _number(low: float, high: float, suffix: str, step: float) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(3)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    return box


class LockinPanel(QWidget):
    log = Signal(str, str)

    def __init__(self, get_cfg: Callable, get_acq: Callable, parent=None) -> None:
        super().__init__(parent)
        self._get_cfg, self._get_acq = get_cfg, get_acq
        self._known: dict | None = None  # the settings last read from the instrument
        self.model: str | None = None
        self.driver = None
        self.fields: dict[str, tuple[str, QWidget]] = {}  # settings key -> (kind, control)

        lay = QVBoxLayout(self)
        self.box = QGroupBox()
        self.form = QFormLayout(self.box)
        lay.addWidget(self.box)

        row = QHBoxLayout()
        self.read_btn = QPushButton("Read from lock-in")
        self.manual_btn = QPushButton("Lab-manual values")
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
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #5f6368;")
        lay.addWidget(self.note)
        lay.addStretch(1)

        self.read_btn.clicked.connect(self._read)
        self.manual_btn.clicked.connect(self._fill_from_manual)
        self.apply_btn.clicked.connect(self._apply)
        self._tasks = []
        self.set_model(get_cfg().lockin.model)

    def set_model(self, model: str) -> None:
        """Build the controls for this model (the one chosen on the Instruments tab)."""
        driver = LOCKIN_MODELS.get(model)
        if driver is None or model == self.model:
            return
        self.model, self.driver, self._known = model, driver, None
        while self.form.rowCount():
            self.form.removeRow(0)
        self.fields = {}
        for key, label, kind, options in driver.PANEL:
            if kind in ("choice", "index"):
                widget = _combo(options)
            elif kind == "khz":
                widget = _number(*options, " kHz", 1.0)
            elif kind == "volts":
                widget = _number(*options, " V", 0.1)
            else:
                widget = QCheckBox(options)
            self.fields[key] = (kind, widget)
            self.form.addRow(label, widget)
        self.box.setTitle(f"{driver.MODEL} settings")
        self.manual_btn.setToolTip(f"Fill in {driver.SETUP_NAME} (capacitance measurement). "
                                   "Nothing is sent until Apply.")
        self.note.setText(driver.PANEL_NOTE + " Changes made here during a run are written "
                          "into the data file, as are front-panel changes, within a minute.")
        self.checks.setText("")
        self.status.setText("Read the lock-in to see its settings.")

    def widget(self, key: str) -> QWidget:
        return self.fields[key][1]

    # --- values in the controls ---------------------------------------------------------
    def values(self) -> dict:
        out = {}
        for key, (kind, w) in self.fields.items():
            if kind == "choice":
                out[key] = w.currentData()
            elif kind == "index":
                out[key] = w.currentIndex()
            elif kind == "khz":
                out[key] = round(w.value() * 1000.0, 6)
            elif kind == "volts":
                out[key] = round(w.value(), 3)
            else:
                out[key] = w.isChecked()
        return out

    def _set(self, key: str, value) -> None:
        kind, w = self.fields[key]
        if kind == "choice":
            w.setCurrentIndex(max(0, w.findData(value)))
        elif kind == "index":
            w.setCurrentIndex(int(value))
        elif kind == "khz":
            w.setValue(value / 1000.0)
        elif kind == "volts":
            w.setValue(value)
        else:
            w.setChecked(bool(value))

    def show_settings(self, s: dict) -> None:
        """Fill the controls from settings read from the lock-in, and check them."""
        if self.driver is None or not set(self.fields) <= set(s):
            return  # another model's settings
        self._known = dict(s)
        for key in self.fields:
            if s.get(key) is not None:
                self._set(key, s[key])
        self.checks.setText(setup_table_html(check_setup(s, self.driver.SETUP_TABLE),
                                             self.driver.SETUP_NAME, self.driver.SETUP_NOTE))

    def _fill_from_manual(self) -> None:
        self.set_model(self._get_cfg().lockin.model)
        for key, _, wanted, _, _ in self.driver.SETUP_TABLE:
            value = wanted[0] if isinstance(wanted, tuple) else wanted
            if key in self.fields:
                self._set(key, value)
            elif f"{key}_index" in self.fields:  # the table names the label: sensitivity "1 V"
                kind, w = self.fields[f"{key}_index"]
                w.setCurrentIndex(w.findData(value))
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
        self.set_model(model)
        if model not in LOCKIN_MODELS:
            self.status.setText(f"Unknown lock-in model {model!r}: choose one on the Instruments tab.")
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
            planned = self.driver.commands(changes)
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
