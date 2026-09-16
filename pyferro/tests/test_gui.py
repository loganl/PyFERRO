import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from ferro import config  # noqa: E402
from ferro.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("FERRO_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    cfg = config.AppConfig(simulate=True)
    cfg.run.output_dir = str(tmp_path / "data")
    cfg.run.interval_s = 0.2
    win = MainWindow(cfg)
    win.resize(1400, 900)
    qtbot.addWidget(win)
    win.show()
    yield win
    if win.running:
        win.acq.stop()


def test_record_requires_a_name(window, qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    window.sample.setText("")
    qtbot.mouseClick(window.record_btn, Qt.LeftButton)
    assert shown and not window.running and not window.record_btn.isChecked()


def test_simulated_record_stop_cycle(window, qtbot, tmp_path):
    window.sample.setText("gui test")
    qtbot.mouseClick(window.record_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._rows_written >= 5, timeout=15000)
    assert window.record_btn.isChecked()
    assert not window.start_btn.isEnabled()
    assert window.tiles["T"].value.text().endswith("°C")
    qtbot.wait(400)
    window._redraw()
    shot = os.environ.get("FERRO_SCREENSHOT")
    if shot:
        window.grab().save(shot)

    qtbot.mouseClick(window.stop_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.running and not window._stopping, timeout=15000)
    assert window.start_btn.isEnabled() and not window.record_btn.isChecked()

    files = list((tmp_path / "data").glob("gui_test_*.txt"))
    assert len(files) == 1
    data = np.loadtxt(files[0])
    assert data.shape[0] >= 5 and np.isfinite(data[:, :3]).all()
    assert config.load().run.sample == "gui test"  # settings persisted


def test_monitor_then_record_creates_separate_files(window, qtbot, tmp_path):
    window.sample.setText("two")
    qtbot.mouseClick(window.start_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.buffer.n >= 2, timeout=10000)
    for _ in range(2):
        qtbot.mouseClick(window.record_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: window._rec_path is not None, timeout=10000)
        qtbot.wait(1100)  # file names have 1 s resolution; unique_path handles clashes anyway
        qtbot.mouseClick(window.record_btn, Qt.LeftButton)
        qtbot.waitUntil(lambda: window._rec_path is None, timeout=10000)
    qtbot.mouseClick(window.stop_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.running, timeout=15000)
    assert len(list((tmp_path / "data").glob("two_*.txt"))) == 2
