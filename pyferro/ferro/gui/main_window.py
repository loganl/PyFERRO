"""Main window: run settings, live readouts, plots and log."""

from __future__ import annotations

import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config
from ..acquisition import Acquisition
from ..datafile import check_writable
from .setup_panel import SetupPanel
from .widgets import Readout, StatusLight, format_si, run_task

THEMES = {
    "light": {
        "tile_bg": "#f6f7f9", "tile_border": "#dde1e6", "tile_text": "#1a1a1a",
        "tile_sub": "#5f6368", "alert_bg": "#fdecea", "alert_border": "#d64545",
        "alert_text": "#8c1d18", "log_bg": "#ffffff", "log_text": "#1a1a1a",
        "log_stamp": "#888888", "log_warning": "#b7791f", "log_error": "#d64545",
        "plot_bg": "#ffffff", "plot_fg": "#333333", "rec": "#d64545",
        "heat": "#d64545", "cool": "#2b6cb0", "flat": "#5f6368",
    },
    "dark": {
        "tile_bg": "#2a2d31", "tile_border": "#3c4046", "tile_text": "#f2f3f5",
        "tile_sub": "#a8adb4", "alert_bg": "#4a1f1c", "alert_border": "#ef5350",
        "alert_text": "#ffb4ab", "log_bg": "#1e2124", "log_text": "#e6e8ea",
        "log_stamp": "#8a9098", "log_warning": "#e0b252", "log_error": "#ff6b6b",
        "plot_bg": "#1e2124", "plot_fg": "#d0d3d6", "rec": "#ff6b6b",
        "heat": "#ef5350", "cool": "#64b5f6", "flat": "#9aa0a6",
    },
}


def detect_theme() -> str:
    """"dark" or "light", following the system theme."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
        if scheme == Qt.ColorScheme.Light:
            return "light"
    except (AttributeError, RuntimeError):  # older Qt without colorScheme()
        pass
    app = QApplication.instance()
    if app is not None and app.palette().color(QPalette.Window).lightness() < 128:
        return "dark"
    return "light"


def style_sheet(c: dict) -> str:
    """The parts that must not inherit their colour from the system theme.

    The readout tiles and the log panel paint their own background, so their text
    colour has to be set alongside it - otherwise a dark theme puts white text on a
    light tile, and a light theme puts dark text on a dark one.
    """
    return f"""
QMainWindow, QWidget {{ font-size: 13px; }}
#readout {{ background: {c['tile_bg']}; border: 1px solid {c['tile_border']}; border-radius: 8px; }}
#readout[alert="true"] {{ background: {c['alert_bg']}; border-color: {c['alert_border']}; }}
#readoutTitle {{ color: {c['tile_sub']}; font-size: 11px; text-transform: uppercase; }}
#readoutValue {{ color: {c['tile_text']}; font-size: 24px; font-weight: 600;
                font-family: Menlo, Consolas, monospace; }}
#readout[alert="true"] #readoutValue {{ color: {c['alert_text']}; }}
#readoutSub {{ color: {c['tile_sub']}; font-size: 11px; }}
#simBanner {{ background: #d69e2e; color: #1a1a1a; font-weight: 600; padding: 4px; }}
#recBanner {{ color: {c['rec']}; font-weight: 600; }}
QPlainTextEdit {{ background: {c['log_bg']}; color: {c['log_text']}; }}
QPushButton#startBtn, QPushButton#recordBtn, QPushButton#stopBtn {{ padding: 8px 16px; font-weight: 600; }}
QPushButton#recordBtn:checked {{ background: {c['rec']}; color: #ffffff; }}
"""


MAX_POINTS = 500_000


class Bridge(QObject):
    """Thread-safe hop from the acquisition thread into the GUI thread."""

    sample = Signal(dict)
    log = Signal(str, str)
    status = Signal(str, str, str)
    recording = Signal(object)


class PlotBuffer:
    def __init__(self) -> None:
        self.keys = ("time_s", "T_C", "X_V", "Y_V", "direction")
        self.clear()

    def clear(self) -> None:
        self.data = {k: np.empty(4096) for k in self.keys}
        self.n = 0
        self._moving = 1

    def append(self, row: dict) -> None:
        if self.n >= MAX_POINTS:  # keep the newest half
            for k in self.keys:
                self.data[k][: self.n // 2] = self.data[k][self.n // 2 : self.n]
            self.n //= 2
        if self.n == len(self.data["time_s"]):
            for k in self.keys:
                self.data[k] = np.concatenate([self.data[k], np.empty_like(self.data[k])])
        d = row.get("direction", 0)
        if d:
            self._moving = d
        values = {**row, "direction": self._moving}
        for k in self.keys:
            v = values.get(k, math.nan)
            self.data[k][self.n] = math.nan if v is None else v
        self.n += 1

    def view(self, key: str) -> np.ndarray:
        return self.data[key][: self.n]


class MainWindow(QMainWindow):
    def __init__(self, cfg: config.AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.acq: Acquisition | None = None
        self.bridge = Bridge()
        self.buffer = PlotBuffer()
        self._dirty = False
        self._record_started = 0.0
        self._rec_path: str | None = None
        self._rows_written = 0
        self._stopping = False
        self.setWindowTitle(f"PyFERRO {__version__} — phase-transition data acquisition")
        self.theme = THEMES[detect_theme()]
        self.setStyleSheet(style_sheet(self.theme))
        self.heat_pen = pg.mkPen(self.theme["heat"], width=1.5)
        self.cool_pen = pg.mkPen(self.theme["cool"], width=1.5)
        self.flat_pen = pg.mkPen(self.theme["flat"], width=1.5)
        self.resize(1400, 900)
        self._build()
        self._connect()
        self.setup.load(cfg)
        self._load_run(cfg)
        self._update_buttons()
        self._redraw_timer = QTimer(self, interval=250, timeout=self._redraw)
        self._redraw_timer.start()
        self._clock = QTimer(self, interval=1000, timeout=self._tick)
        self._clock.start()
        self.log("info", f"PyFERRO {__version__} ready. Settings: {config.config_path()}")

    # --- layout ---------------------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        self.setCentralWidget(central)

        self.sim_banner = QLabel("SIMULATION MODE — data are not from the instruments")
        self.sim_banner.setObjectName("simBanner")
        self.sim_banner.setAlignment(Qt.AlignCenter)
        root.addWidget(self.sim_banner)

        bar = QHBoxLayout()
        self.start_btn = QPushButton("▶  Start monitoring")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.setToolTip("Connect and show live data without saving (F5)")
        self.record_btn = QPushButton("●  Record")
        self.record_btn.setObjectName("recordBtn")
        self.record_btn.setCheckable(True)
        self.record_btn.setToolTip("Save to a new data file (Ctrl+R). Starts monitoring if needed.")
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.clear_btn = QPushButton("Clear plots")
        for b in (self.start_btn, self.record_btn, self.stop_btn, self.clear_btn):
            bar.addWidget(b)
        self.rec_label = QLabel("")
        self.rec_label.setObjectName("recBanner")
        self.rec_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar.addWidget(self.rec_label, 1)
        self.lights = {"lockin": StatusLight("Lock-in"), "pid": StatusLight("CND3"), "dmm": StatusLight("Multimeter")}
        for light in self.lights.values():
            bar.addWidget(light)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._build_run_tab()), "Run")
        self.setup = SetupPanel()
        tabs.addTab(self._scroll(self.setup), "Instruments")
        tabs.setMinimumWidth(380)
        split.addWidget(tabs)

        right = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tiles = QHBoxLayout()
        self.tiles = {k: Readout(t) for k, t in (
            ("T", "Sample temperature"), ("SV", "Setpoint"), ("ramp", "Ramp"),
            ("X", "Lock-in X"), ("Y", "Lock-in Y"), ("R", "R / θ"))}
        for tile in self.tiles.values():
            tiles.addWidget(tile)
        tl.addLayout(tiles)

        pg.setConfigOptions(antialias=True, background=self.theme["plot_bg"],
                            foreground=self.theme["plot_fg"])
        self.plots = pg.GraphicsLayoutWidget()
        self.p_time = self.plots.addPlot(row=0, col=0, colspan=2, title="Temperature vs time")
        self.p_x = self.plots.addPlot(row=1, col=0, title="X vs temperature")
        self.p_y = self.plots.addPlot(row=1, col=1, title="Y vs temperature")
        for p in (self.p_time, self.p_x, self.p_y):
            p.showGrid(x=True, y=True, alpha=0.25)
            p.getAxis("bottom").enableAutoSIPrefix(False)
        self.p_time.getAxis("left").enableAutoSIPrefix(False)
        self.p_time.setLabel("left", "T (°C)")
        self.p_time.setLabel("bottom", "time (min)")
        for p, name in ((self.p_x, "X"), (self.p_y, "Y")):
            p.setLabel("left", name, units="V")  # pyqtgraph picks µV / mV automatically
            p.setLabel("bottom", "T (°C)")
        self.p_x.addLegend(offset=(-10, 10))
        self.c_time = self.p_time.plot(pen=self.flat_pen)
        self.c_x = (self.p_x.plot(pen=self.heat_pen, name="heating"),
                    self.p_x.plot(pen=self.cool_pen, name="cooling"))
        self.c_y = (self.p_y.plot(pen=self.heat_pen), self.p_y.plot(pen=self.cool_pen))
        tl.addWidget(self.plots, 1)
        right.addWidget(top)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        right.addWidget(self.log_view)
        right.setSizes([700, 160])
        split.addWidget(right)
        split.setSizes([400, 1000])

        act = QAction(self, shortcut=QKeySequence("F5"), triggered=self._start_monitoring)
        self.addAction(act)
        act = QAction(self, shortcut=QKeySequence("Ctrl+R"), triggered=lambda: self.record_btn.click())
        self.addAction(act)

    def _scroll(self, widget: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def _build_run_tab(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        form = QFormLayout()
        outer.addLayout(form)
        outer.addStretch(1)  # keep rows compact instead of spreading over the tab
        self.sample = QLineEdit()
        self.sample.setPlaceholderText("e.g. BTO_1V_37kHz (used in the file name)")
        form.addRow("Sample / run name", self.sample)
        self.operator = QLineEdit()
        self.operator.setPlaceholderText("Group / names")
        form.addRow("Operator", self.operator)
        self.drive = QLineEdit()
        self.drive.setPlaceholderText("e.g. 2.00 V, 25 kHz, R0 = 600 Ω")
        form.addRow("Drive / circuit", self.drive)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Anything needed to reconstruct the measurement (saved in the file header)")
        self.notes.setFixedHeight(80)
        form.addRow("Notes", self.notes)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.out_dir = QLineEdit()
        browse = QPushButton("Browse…")
        opener = QPushButton("Open")
        h.addWidget(self.out_dir, 1)
        h.addWidget(browse)
        h.addWidget(opener)
        browse.clicked.connect(self._browse)
        opener.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_dir.text())))
        form.addRow("Save folder", row)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 600)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setToolTip("Time between readings. Keep it longer than ~5× the lock-in time constant.")
        form.addRow("Reading interval", self.interval)
        self.min_dt = QDoubleSpinBox()
        self.min_dt.setRange(0, 10)
        self.min_dt.setDecimals(2)
        self.min_dt.setSuffix(" °C")
        self.min_dt.setSpecialValueText("off — save every reading")
        self.min_dt.setToolTip("Only save a row when T has changed by at least this much (the old LabVIEW behaviour).")
        form.addRow("Save only if ΔT ≥", self.min_dt)
        self.temp_source = QComboBox()
        self.temp_source.addItem("CND3 controller (RS-485 probe)", "pid")
        self.temp_source.addItem("Multimeter Pt100 (GPIB)", "dmm")
        self.temp_source.setToolTip("Which thermometer fills the T_C column. Can be changed "
                                    "while monitoring; locked while recording so that one file "
                                    "keeps one temperature source.")
        form.addRow("Temperature from", self.temp_source)
        self.max_temp = QDoubleSpinBox()
        self.max_temp.setRange(30, 400)
        self.max_temp.setSuffix(" °C")
        self.max_temp.setToolTip("Warn when the sample chamber exceeds this temperature (manual: do not exceed 160 °C).")
        form.addRow("Chamber limit warning", self.max_temp)
        self.legacy = QCheckBox("Old LabVIEW file format (T, X, Y only; settings in a .json file)")
        form.addRow(self.legacy)
        hint = QLabel("Files are never overwritten: each recording creates <i>name_YYYYMMDD_HHMMSS.txt</i> "
                      "with a header describing the instrument settings.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5f6368;")
        form.addRow(hint)
        return w

    # --- wiring ------------------------------------------------------------------------
    def _connect(self) -> None:
        self.start_btn.clicked.connect(self._start_monitoring)
        self.record_btn.clicked.connect(self._toggle_record)
        self.stop_btn.clicked.connect(self._stop)
        self.clear_btn.clicked.connect(self._clear)
        self.setup.log.connect(self.log)
        self.setup.simulate.toggled.connect(lambda on: self.sim_banner.setVisible(on))
        self.bridge.sample.connect(self._on_sample)
        self.bridge.log.connect(self.log)
        self.bridge.status.connect(lambda k, s, m: self.lights[k].set_state(s, m))
        self.bridge.recording.connect(self._on_recording)
        # settings that are safe to change live
        self.interval.valueChanged.connect(lambda v: setattr(self.cfg.run, "interval_s", v))
        self.min_dt.valueChanged.connect(lambda v: setattr(self.cfg.run, "min_delta_t", v))
        self.max_temp.valueChanged.connect(lambda v: setattr(self.cfg.run, "max_temp_c", v))
        self.temp_source.currentIndexChanged.connect(self._temp_source_changed)

    def _load_run(self, cfg: config.AppConfig) -> None:
        r = cfg.run
        self.sample.setText(r.sample)
        self.operator.setText(r.operator)
        self.drive.setText(r.drive)
        self.notes.setPlainText(r.notes)
        self.out_dir.setText(r.output_dir)
        self.interval.setValue(r.interval_s)
        self.min_dt.setValue(r.min_delta_t)
        self.temp_source.setCurrentIndex(0 if r.temp_source == "pid" else 1)
        self.max_temp.setValue(r.max_temp_c)
        self.legacy.setChecked(r.legacy_format)
        self.sim_banner.setVisible(cfg.simulate)

    def _apply(self) -> config.AppConfig:
        r = self.cfg.run
        r.sample = self.sample.text().strip()
        r.operator = self.operator.text().strip()
        r.drive = self.drive.text().strip()
        r.notes = self.notes.toPlainText().strip()
        r.output_dir = self.out_dir.text().strip() or config.default_data_dir()
        r.interval_s = self.interval.value()
        r.min_delta_t = self.min_dt.value()
        r.temp_source = self.temp_source.currentData()
        r.max_temp_c = self.max_temp.value()
        r.legacy_format = self.legacy.isChecked()
        if not self.running:
            self.setup.apply(self.cfg)
        config.save(self.cfg)
        return self.cfg

    # --- actions ---------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.acq is not None and self.acq.running

    def _start_monitoring(self, record: bool = False) -> None:
        if self.running or self._stopping:
            return
        cfg = self._apply()
        if cfg.run.temp_source == "dmm" and not cfg.dmm.enabled:
            cfg.dmm.enabled = True
            self.setup.dmm_enabled.setChecked(True)
        for key, light in self.lights.items():
            light.set_state("busy" if key != "dmm" or cfg.dmm.enabled else "off", "connecting…")
        self.acq = Acquisition(cfg, on_sample=self.bridge.sample.emit, on_log=self.bridge.log.emit,
                               on_status=self.bridge.status.emit, on_recording=self.bridge.recording.emit)
        self.setup.set_locked(True)
        self.acq.start(record=record)
        self._update_buttons()

    def _toggle_record(self) -> None:
        want = self.record_btn.isChecked()
        if want:
            if not self._ready_to_record():
                self.record_btn.setChecked(False)
                return
            if self.running:
                self._apply()
                self.acq.set_recording(True)
            else:
                self._start_monitoring(record=True)
        elif self.acq is not None:
            self.acq.set_recording(False)
        self._update_buttons()

    def _ready_to_record(self) -> bool:
        if not self.sample.text().strip():
            QMessageBox.information(self, "Name the run", "Enter a sample / run name first — it becomes the file name.")
            self.sample.setFocus()
            return False
        folder = Path(self.out_dir.text().strip() or config.default_data_dir())
        try:
            check_writable(folder)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot save here", str(exc))
            return False
        return True

    def _stop(self) -> None:
        if not self.running or self._stopping:
            return
        if self.acq.recording:
            answer = QMessageBox.question(self, "Stop recording?",
                                          "Data are being recorded. Stop and close the data file?")
            if answer != QMessageBox.Yes:
                return
        self._stopping = True
        self._update_buttons()
        acq = self.acq
        run_task(acq.stop, lambda _: self._stopped(), lambda msg: (self.log("error", msg), self._stopped()))

    def _temp_source_changed(self) -> None:
        source = self.temp_source.currentData()
        self.cfg.run.temp_source = source
        if source == "dmm" and not self.cfg.dmm.enabled:
            self.cfg.dmm.enabled = True
            self.setup.dmm_enabled.setChecked(True)
        if self.running:
            self.log("info", f"Temperature now taken from the "
                             f"{'CND3 controller' if source == 'pid' else 'multimeter Pt100'}")

    def _stopped(self) -> None:
        self._stopping = False
        self.setup.set_locked(False)
        for light in self.lights.values():
            light.set_state("off")
        self._update_buttons()

    def _clear(self) -> None:
        self.buffer.clear()
        self._dirty = True

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose save folder", self.out_dir.text())
        if folder:
            self.out_dir.setText(folder)

    def _update_buttons(self) -> None:
        running = self.running
        recording = bool(self.acq and (self.acq.recording or self._rec_path))
        self.start_btn.setEnabled(not running and not self._stopping)
        self.stop_btn.setEnabled(running and not self._stopping)
        self.record_btn.setEnabled(not self._stopping)
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(recording and running)
        self.record_btn.setText("●  Recording — click to stop" if recording and running else "●  Record")
        self.record_btn.blockSignals(False)
        # The source may change while monitoring, but not once a file is open.
        self.temp_source.setEnabled(not (recording and running))

    # --- data from acquisition -----------------------------------------------------------
    def _on_recording(self, path) -> None:
        self._rec_path = path
        if path:
            self._record_started = time.monotonic()
            self._rows_written = 0
        self._update_buttons()
        self._tick()

    def _on_sample(self, row: dict) -> None:
        self.buffer.append(row)
        self._dirty = True
        if self.acq and self.acq.writer:
            self._rows_written = self.acq.writer.rows

        t, sv = row.get("T_C", math.nan), row.get("SV_C", math.nan)
        over = bool(row.get("over_temp"))
        self.tiles["T"].set(f"{t:.2f} °C" if not math.isnan(t) else "—",
                            "ABOVE CHAMBER LIMIT" if over else ("controller PV" if self.cfg.run.temp_source == "pid" else "Pt100"),
                            alert=over or math.isnan(t))
        pv = row.get("PV_C", math.nan)
        self.tiles["SV"].set(f"{sv:.1f} °C" if not math.isnan(sv) else "—",
                             f"PV {pv:.1f} °C" if not math.isnan(pv) else "")
        slope = row.get("slope_c_per_min", math.nan)
        arrow = {1: "▲ heating", -1: "▼ cooling", 0: "● steady"}[row.get("direction", 0)]
        self.tiles["ramp"].set(f"{slope:+.2f}" if not math.isnan(slope) else "—", f"°C/min  {arrow}  seg {row.get('segment', 0)}")
        ovl = bool(row.get("flags", 0) & 1)
        pct = row.get("percent_fs", math.nan)
        sens = row.get("sens_V", math.nan)
        sub = f"{pct:.0f}% of {format_si(sens, 'V', 3)}" if not math.isnan(pct) else "no reading"
        self.tiles["X"].set(format_si(row.get("X_V"), "V"), "OVERLOAD — raise sensitivity" if ovl else sub, alert=ovl)
        self.tiles["Y"].set(format_si(row.get("Y_V"), "V"), sub, alert=ovl)
        theta = row.get("theta_deg", math.nan)
        self.tiles["R"].set(format_si(row.get("R_V"), "V"), f"θ = {theta:.1f}°" if not math.isnan(theta) else "")

    def _redraw(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        b = self.buffer
        t_min = b.view("time_s") / 60.0
        temp = b.view("T_C")
        self.c_time.setData(t_min, temp, connect="finite")
        d = b.view("direction")
        for curves, key in ((self.c_x, "X_V"), (self.c_y, "Y_V")):
            y = b.view(key)
            for curve, sign in zip(curves, (1, -1)):
                mask = d == sign
                curve.setData(np.where(mask, temp, np.nan), np.where(mask, y, np.nan), connect="finite")

    def _tick(self) -> None:
        if self._rec_path and self.running:
            elapsed = int(time.monotonic() - self._record_started)
            self.rec_label.setText(f"● REC {elapsed // 3600:d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}  "
                                   f"{self._rows_written} rows → {self._rec_path}")
        elif self.running:
            self.rec_label.setText("Monitoring — not saving")
        else:
            self.rec_label.setText("")

    def log(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        color = {"error": self.theme["log_error"], "warning": self.theme["log_warning"]}.get(
            level, self.theme["log_text"])
        safe = message.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br>")
        self.log_view.appendHtml(f'<span style="color:{self.theme["log_stamp"]}">{stamp}</span> '
                                 f'<span style="color:{color}">{safe}</span>')
        if level == "error" and self.isVisible() and "chamber limit" in message:
            QApplication.beep()

    def closeEvent(self, event) -> None:
        if self.running:
            if self.acq.recording:
                answer = QMessageBox.question(self, "Quit FERRO?", "Data are being recorded. Stop recording and quit?")
                if answer != QMessageBox.Yes:
                    event.ignore()
                    return
            self.acq.stop()
        self._apply()
        event.accept()


def run(simulate: bool = False) -> int:
    if sys.platform == "win32":
        try:  # crisp text and a proper taskbar icon grouping on Windows
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("phy445.ferro")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("PyFERRO")
    app.setStyle("Fusion")
    # Fusion keeps the system palette, so the window follows the light/dark theme;
    # style_sheet() colours the parts that paint their own background.
    app.setStyleSheet(style_sheet(THEMES[detect_theme()]))
    cfg = config.load()
    if simulate:
        cfg.simulate = True
    win = MainWindow(cfg)
    win.show()
    if os.environ.get("FERRO_SMOKE_TEST"):
        QTimer.singleShot(int(os.environ["FERRO_SMOKE_TEST"]), app.quit)
    return app.exec()
