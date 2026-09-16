"""Instrument connection settings with Test / Auto-detect buttons."""

from __future__ import annotations

import copy

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..acquisition import open_dmm, open_lockin, open_pid
from ..config import AppConfig
from ..instruments.cnd3 import BAUD_RATES, autodetect
from ..transports import list_visa_resources
from .widgets import PortCombo, run_task

FORMATS = ["7E1", "8N1", "7O1", "8E1", "7N2", "8N2", "7E2", "7O2", "8O1"]


def _result_label() -> QLabel:
    lab = QLabel("")
    lab.setWordWrap(True)
    lab.setObjectName("testResult")
    return lab


class _Progress(QObject):
    text = Signal(str)


class SetupPanel(QWidget):
    log = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tasks = []
        self._stop_detect = False
        lay = QVBoxLayout(self)

        self.simulate = QCheckBox("Simulation mode (no hardware — for practice only)")
        lay.addWidget(self.simulate)

        # --- lock-in ----------------------------------------------------------
        box = QGroupBox("Lock-in amplifier — EG&G 5302")
        form = QFormLayout(box)
        self.li_iface = QComboBox()
        self.li_iface.addItem("GPIB (NI adapter)", "visa")
        self.li_iface.addItem("RS-232 serial port", "serial")
        form.addRow("Connection", self.li_iface)
        self.li_stack = QStackedWidget()
        visa_row = QWidget()
        h = QHBoxLayout(visa_row)
        h.setContentsMargins(0, 0, 0, 0)
        self.li_resource = QComboBox()
        self.li_resource.setEditable(True)
        self.li_find = QPushButton("Find")
        self.li_find.setToolTip("List GPIB/VISA instruments")
        h.addWidget(self.li_resource, 1)
        h.addWidget(self.li_find)
        self.li_serial = PortCombo()
        self.li_stack.addWidget(visa_row)
        self.li_stack.addWidget(self.li_serial)
        form.addRow("Address", self.li_stack)
        self.li_test = QPushButton("Test lock-in")
        self.li_result = _result_label()
        form.addRow(self.li_test, self.li_result)
        lay.addWidget(box)

        # --- CND3 ---------------------------------------------------------------
        box = QGroupBox("Temperature controller — Omega CND3 (RS-485)")
        form = QFormLayout(box)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.pid_port = PortCombo()
        self.pid_refresh = QPushButton("Refresh")
        h.addWidget(self.pid_port, 1)
        h.addWidget(self.pid_refresh)
        form.addRow("USB-RS485 port", row)
        self.pid_addr = QSpinBox()
        self.pid_addr.setRange(1, 247)
        form.addRow("Address (C-NO)", self.pid_addr)
        self.pid_mode = QComboBox()
        self.pid_mode.addItem("ASCII (factory default)", "ascii")
        self.pid_mode.addItem("RTU", "rtu")
        form.addRow("Protocol", self.pid_mode)
        self.pid_baud = QComboBox()
        for b in BAUD_RATES:
            self.pid_baud.addItem(str(b), b)
        form.addRow("Baud (BPS)", self.pid_baud)
        self.pid_format = QComboBox()
        self.pid_format.addItems(FORMATS)
        self.pid_format.setToolTip("Data bits / parity / stop bits (LEN / PRTY / STOP)")
        form.addRow("Format", self.pid_format)
        btns = QWidget()
        h = QHBoxLayout(btns)
        h.setContentsMargins(0, 0, 0, 0)
        self.pid_test = QPushButton("Test controller")
        self.pid_detect = QPushButton("Auto-detect settings")
        h.addWidget(self.pid_test)
        h.addWidget(self.pid_detect)
        self.pid_result = _result_label()
        form.addRow(btns)
        form.addRow(self.pid_result)
        lay.addWidget(box)

        # --- DMM ------------------------------------------------------------------
        box = QGroupBox("Multimeter — HP 34401A (optional second Pt100)")
        form = QFormLayout(box)
        self.dmm_enabled = QCheckBox("Also read the multimeter")
        form.addRow(self.dmm_enabled)
        self.dmm_resource = QComboBox()
        self.dmm_resource.setEditable(True)
        form.addRow("GPIB address", self.dmm_resource)
        self.dmm_mode = QComboBox()
        self.dmm_mode.addItem("Pt100 in 4-wire ohms → °C", "pt100")
        self.dmm_mode.addItem("Reading is already °C", "celsius")
        form.addRow("Reading", self.dmm_mode)
        self.dmm_r0 = QDoubleSpinBox()
        self.dmm_r0.setRange(10, 2000)
        self.dmm_r0.setSuffix(" Ω at 0 °C")
        form.addRow("R0", self.dmm_r0)
        self.dmm_test = QPushButton("Test multimeter")
        self.dmm_result = _result_label()
        form.addRow(self.dmm_test, self.dmm_result)
        lay.addWidget(box)
        lay.addStretch(1)

        for combo, default in ((self.li_resource, "GPIB0::12::INSTR"), (self.dmm_resource, "GPIB0::24::INSTR")):
            combo.addItem(default)

        self.li_iface.currentIndexChanged.connect(lambda i: self.li_stack.setCurrentIndex(i))
        self.li_find.clicked.connect(self._find_visa)
        self.pid_refresh.clicked.connect(lambda: (self.pid_port.refresh(), self.li_serial.refresh()))
        self.li_test.clicked.connect(self._test_lockin)
        self.pid_test.clicked.connect(self._test_pid)
        self.pid_detect.clicked.connect(self._detect_pid)
        self.dmm_test.clicked.connect(self._test_dmm)
        self._config = AppConfig()

    # --- config binding --------------------------------------------------------
    def load(self, cfg: AppConfig) -> None:
        self._config = cfg
        self.simulate.setChecked(cfg.simulate)
        self.li_iface.setCurrentIndex(0 if cfg.lockin.interface == "visa" else 1)
        self.li_resource.setEditText(cfg.lockin.resource)
        self.li_serial.set_device(cfg.lockin.serial_port)
        self.pid_port.set_device(cfg.pid.port)
        self.pid_addr.setValue(cfg.pid.address)
        self.pid_mode.setCurrentIndex(0 if cfg.pid.mode == "ascii" else 1)
        self.pid_baud.setCurrentIndex(max(0, self.pid_baud.findData(cfg.pid.baudrate)))
        fmt = f"{cfg.pid.bytesize}{cfg.pid.parity}{cfg.pid.stopbits}"
        self.pid_format.setCurrentIndex(max(0, self.pid_format.findText(fmt)))
        self.dmm_enabled.setChecked(cfg.dmm.enabled)
        self.dmm_resource.setEditText(cfg.dmm.resource)
        self.dmm_mode.setCurrentIndex(0 if cfg.dmm.mode == "pt100" else 1)
        self.dmm_r0.setValue(cfg.dmm.r0)

    def apply(self, cfg: AppConfig) -> AppConfig:
        cfg.simulate = self.simulate.isChecked()
        cfg.lockin.interface = self.li_iface.currentData()
        cfg.lockin.resource = self.li_resource.currentText().strip()
        cfg.lockin.serial_port = self.li_serial.device()
        cfg.pid.port = self.pid_port.device()
        cfg.pid.address = self.pid_addr.value()
        cfg.pid.mode = self.pid_mode.currentData()
        cfg.pid.baudrate = self.pid_baud.currentData()
        fmt = self.pid_format.currentText()
        cfg.pid.bytesize, cfg.pid.parity, cfg.pid.stopbits = int(fmt[0]), fmt[1], int(fmt[2])
        cfg.dmm.enabled = self.dmm_enabled.isChecked()
        cfg.dmm.resource = self.dmm_resource.currentText().strip()
        cfg.dmm.mode = self.dmm_mode.currentData()
        cfg.dmm.r0 = self.dmm_r0.value()
        return cfg

    def set_locked(self, locked: bool) -> None:
        for kind in (QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton):
            for w in self.findChildren(kind):
                w.setEnabled(not locked)

    # --- actions -------------------------------------------------------------------
    def _snapshot(self) -> AppConfig:
        return self.apply(copy.deepcopy(self._config))

    def _start(self, button: QPushButton, label: QLabel, fn, fmt) -> None:
        button.setEnabled(False)
        label.setStyleSheet("")
        label.setText("Testing…")

        def done(result):
            button.setEnabled(True)
            label.setStyleSheet("color: #1f9d55;")
            label.setText("✔ " + fmt(result))
            self.log.emit("info", label.text())

        def failed(msg):
            button.setEnabled(True)
            label.setStyleSheet("color: #d64545;")
            label.setText("✘ " + msg)
            self.log.emit("warning", msg)

        self._tasks.append(run_task(fn, done, failed))

    def _test_lockin(self) -> None:
        cfg = self._snapshot()

        def work():
            li = open_lockin(cfg)
            try:
                li.check()
                return li.settings()
            finally:
                li.close()

        self._start(self.li_test, self.li_result, work, lambda s: (
            f"5302 found — sensitivity {s['sensitivity']}, TC {s['time_constant']}, "
            f"reference {s['frequency_hz']:.4g} Hz" + (", EXPAND on" if s["expand"] else "")))

    def _test_pid(self) -> None:
        cfg = self._snapshot()

        def work():
            pid = open_pid(cfg)
            try:
                return pid.status()
            finally:
                pid.close()

        def fmt(s):
            pv = f"{s['pv_c']:.1f}" if s.get("pv_c") is not None else s.get("sensor_error", "?")
            return (f"CND3 {s['firmware']} — PV {pv} {s['unit']}, SV {s['sv_c']:.1f} {s['unit']}, "
                    f"output {s['output1_percent']:.1f}%, {s['control']}, {s['run_state']}")

        self._start(self.pid_test, self.pid_result, work, fmt)

    def _test_dmm(self) -> None:
        cfg = self._snapshot()

        def work():
            dmm = open_dmm(cfg)
            try:
                return dmm.identify(), dmm.read_raw(), dmm.read_celsius()
            finally:
                dmm.close()

        self._start(self.dmm_test, self.dmm_result, work,
                    lambda r: f"{r[0].split(',')[1] if ',' in r[0] else r[0]} — {r[1]:.4f} → {r[2]:.2f} °C")

    def _detect_pid(self) -> None:
        if self.pid_detect.text().startswith("Stop"):
            self._stop_detect = True
            return
        port = self.pid_port.device()
        if not port:
            self.pid_result.setText("Choose the USB-RS485 port first.")
            return
        self._stop_detect = False
        progress = _Progress()
        progress.text.connect(lambda t: self.pid_result.setText(f"Trying {t}…"))
        self.pid_detect.setText("Stop auto-detect")
        self.pid_test.setEnabled(False)

        def work():
            return autodetect(port, addresses=tuple(range(1, 6)), progress=progress.text.emit,
                              should_stop=lambda: self._stop_detect)

        def done(found):
            self.pid_detect.setText("Auto-detect settings")
            self.pid_test.setEnabled(True)
            if not found:
                self.pid_result.setStyleSheet("color: #d64545;")
                self.pid_result.setText("✘ No CND3 answered. Check wiring (A/B, GND), that the controller "
                                        "is on, and that its communication option (C-SL) is enabled.")
                return
            self.pid_addr.setValue(found["address"])
            self.pid_mode.setCurrentIndex(0 if found["mode"] == "ascii" else 1)
            self.pid_baud.setCurrentIndex(self.pid_baud.findData(found["baudrate"]))
            self.pid_format.setCurrentIndex(self.pid_format.findText(
                f"{found['bytesize']}{found['parity']}{found['stopbits']}"))
            self.pid_result.setStyleSheet("color: #1f9d55;")
            self.pid_result.setText(f"✔ Found CND3 {found['firmware']}: {found['mode'].upper()}, "
                                    f"address {found['address']}, {found['baudrate']} "
                                    f"{found['bytesize']}{found['parity']}{found['stopbits']}")

        def failed(msg):
            self.pid_detect.setText("Auto-detect settings")
            self.pid_test.setEnabled(True)
            self.pid_result.setText("✘ " + msg)

        self._progress = progress
        self._tasks.append(run_task(work, done, failed))

    def _find_visa(self) -> None:
        self.li_find.setEnabled(False)

        def done(found):
            self.li_find.setEnabled(True)
            for combo in (self.li_resource, self.dmm_resource):
                text = combo.currentText()
                combo.clear()
                combo.addItems(found or [text])
                combo.setEditText(text)
            self.li_result.setText("Found: " + (", ".join(found) if found else "no VISA instruments "
                                   "(is NI-VISA / NI-488.2 installed and the GPIB adapter plugged in?)"))

        self._tasks.append(run_task(list_visa_resources, done,
                                    lambda m: (self.li_find.setEnabled(True), self.li_result.setText(m))))
