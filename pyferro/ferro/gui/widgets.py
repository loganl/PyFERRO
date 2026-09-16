"""Reusable Qt widgets and helpers."""

from __future__ import annotations

import math
import traceback

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

COLORS = {"ok": "#1f9d55", "error": "#d64545", "off": "#9aa0a6", "busy": "#d69e2e"}


def format_si(value: float | None, unit: str, digits: int = 4) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if value == 0:
        return f"0 {unit}"
    prefixes = [(1e-9, "n"), (1e-6, "µ"), (1e-3, "m"), (1.0, "")]
    scale, prefix = 1.0, ""
    for s, p in prefixes:
        if abs(value) >= s:
            scale, prefix = s, p
    return f"{value / scale:.{digits}g} {prefix}{unit}"


def list_serial_ports() -> list[tuple[str, str]]:
    """(device, description) for every serial port; FTDI adapters listed first."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    ports = []
    for p in list_ports.comports():
        desc = p.description or ""
        if p.vid == 0x0403:
            desc = f"{desc} — FTDI (likely the Dtech RS-485 adapter)"
        ports.append((p.vid != 0x0403, p.device, desc))
    return [(dev, desc) for _, dev, desc in sorted(ports)]


class StatusLight(QWidget):
    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(6)
        self.dot = QLabel("●")
        self.text = QLabel(name)
        lay.addWidget(self.dot)
        lay.addWidget(self.text)
        self.set_state("off")

    def set_state(self, state: str, message: str = "") -> None:
        self.dot.setStyleSheet(f"color: {COLORS.get(state, COLORS['off'])}; font-size: 16px;")
        self.setToolTip(message or state)


class Readout(QFrame):
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("readout")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.title = QLabel(title)
        self.title.setObjectName("readoutTitle")
        self.value = QLabel("—")
        self.value.setObjectName("readoutValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.sub = QLabel("")
        self.sub.setObjectName("readoutSub")
        for w in (self.title, self.value, self.sub):
            lay.addWidget(w)

    def set(self, text: str, sub: str = "", alert: bool = False) -> None:
        self.value.setText(text)
        self.sub.setText(sub)
        self.setProperty("alert", alert)
        self.style().unpolish(self)
        self.style().polish(self)


class PortCombo(QComboBox):
    """Editable serial-port picker that remembers the typed/selected device."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setMinimumContentsLength(12)
        self.refresh()

    def device(self) -> str:
        idx = self.currentIndex()
        if idx >= 0 and self.itemText(idx) == self.currentText() and self.itemData(idx):
            return self.itemData(idx)
        return self.currentText().split(" ")[0].strip()

    def set_device(self, device: str) -> None:
        for i in range(self.count()):
            if self.itemData(i) == device:
                self.setCurrentIndex(i)
                return
        self.setEditText(device)

    def refresh(self) -> None:
        current = self.device() if self.count() or self.currentText() else ""
        self.clear()
        for dev, desc in list_serial_ports():
            self.addItem(f"{dev}  ({desc})" if desc else dev, dev)
        if current:
            self.set_device(current)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Run a blocking instrument call off the GUI thread."""

    def __init__(self, fn, on_done, on_failed) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.signals.done.connect(on_done)
        self.signals.failed.connect(on_failed)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:
            detail = str(exc) or traceback.format_exc(limit=1)
            self.signals.failed.emit(detail)
        else:
            self.signals.done.emit(result)


# Tasks must stay referenced until their result is delivered, otherwise Python may
# garbage-collect the signal object and the completion callback silently never runs.
_live_tasks: set[Task] = set()


def run_task(fn, on_done, on_failed) -> Task:
    task = Task(fn, on_done, on_failed)
    task.setAutoDelete(False)
    _live_tasks.add(task)
    release = lambda *_: _live_tasks.discard(task)
    task.signals.done.connect(release)
    task.signals.failed.connect(release)
    QThreadPool.globalInstance().start(task)
    return task
