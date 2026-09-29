import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from ferro import config  # noqa: E402
from ferro.datafile import FLAG_TEMP_DISCARDED  # noqa: E402
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
    qtbot.waitUntil(lambda: window._rows_written >= 6, timeout=15000)
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
    kept = data[data[:, 12] != FLAG_TEMP_DISCARDED]  # first temperatures after connecting
    assert kept.shape[0] >= 3 and np.isfinite(kept[:, :3]).all()
    assert config.load().run.sample == "gui test"  # settings persisted


def test_new_points_are_visible_after_clear_plots_even_after_a_zoom(window, qtbot):
    """A mouse-wheel zoom turns auto-range off; Clear then left every new point off-screen."""
    qtbot.mouseClick(window.start_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.buffer.n >= 5, timeout=10000)
    window._redraw()
    vb = window.p_time.getViewBox()
    vb.scaleBy((0.9, 0.9))  # what the mouse wheel does
    assert not any(vb.autoRangeEnabled())
    qtbot.mouseClick(window.clear_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.buffer.n >= 4, timeout=10000)
    window._redraw()
    qtbot.waitUntil(lambda: all(vb.autoRangeEnabled()), timeout=2000)
    t, temp = window.buffer.view("time_s") / 60, window.buffer.view("T_C")
    qtbot.wait(100)
    (x0, x1), (y0, y1) = vb.viewRange()
    assert ((t >= x0) & (t <= x1) & (temp >= y0) & (temp <= y1)).any(), "new points must be on screen"


def test_a_recognised_turn_recolours_the_points_since_the_peak(window):
    base = {"T_C": 100.0, "X_V": 1e-3, "Y_V": 1e-3}
    for t in range(10):
        window._on_sample({**base, "time_s": float(t), "direction": 1})
    window._on_sample({**base, "time_s": 10.0, "direction": -1, "turned_at_s": 6.0})
    assert list(window.buffer.view("direction")) == [1] * 6 + [-1] * 5


def test_a_long_recording_path_does_not_widen_the_window(window):
    """The banner's path once forced the window to 2600 px, off a 1280 px screen."""
    from PySide6.QtWidgets import QApplication

    def min_width():
        QApplication.processEvents()
        window.layout().activate()
        return window.minimumSizeHint().width()

    before = min_width()
    path = "C:\\Users\\LabStudent\\Documents\\FerroData\\" + "BTO_run_" * 20 + "20260929_142436.txt"
    window.rec_label.setText(f"● REC 0:10:12  1234 rows → {path}")
    assert min_width() == before
    assert window.rec_label.text().endswith(".txt") and window.rec_label.toolTip().endswith(".txt")
    shown = super(type(window.rec_label), window.rec_label).text()
    assert "…" in shown and shown.endswith(".txt"), "the middle is elided, the file name kept"


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


@pytest.mark.parametrize("model, name", [("5302", "5302"), ("sr830", "SR830")])
def test_lockin_model_test_button(window, qtbot, model, name):
    setup = window.setup
    setup.li_model.setCurrentIndex(setup.li_model.findData(model))
    qtbot.mouseClick(setup.li_test, Qt.LeftButton)
    qtbot.waitUntil(lambda: setup.li_result.text().startswith(("✔", "✘")), timeout=10000)
    assert setup.li_result.text().startswith(f"✔ {name} found"), setup.li_result.text()


@pytest.mark.parametrize("model, name", [("34401a", "HP 34401A"), ("k199", "Keithley 199")])
def test_dmm_model_test_button(window, qtbot, model, name):
    setup = window.setup
    setup.dmm_model.setCurrentIndex(setup.dmm_model.findData(model))
    qtbot.mouseClick(setup.dmm_test, Qt.LeftButton)
    qtbot.waitUntil(lambda: setup.dmm_result.text().startswith(("✔", "✘")), timeout=10000)
    assert setup.dmm_result.text().startswith(f"✔ {name} found"), setup.dmm_result.text()


def test_other_models_record_and_are_saved(window, qtbot, tmp_path):
    setup = window.setup
    setup.li_model.setCurrentIndex(setup.li_model.findData("sr830"))
    setup.dmm_model.setCurrentIndex(setup.dmm_model.findData("k199"))
    setup.dmm_enabled.setChecked(True)
    window.sample.setText("sr830 k199")
    qtbot.mouseClick(window.record_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: window._rows_written >= 6, timeout=15000)
    qtbot.mouseClick(window.stop_btn, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.running and not window._stopping, timeout=15000)

    path = next((tmp_path / "data").glob("sr830_k199_*.txt"))
    text = path.read_text()
    assert "# lockin_model: SR830" in text and "Keithley 199" in text
    data = np.loadtxt(path)
    kept = data[data[:, 12] != FLAG_TEMP_DISCARDED]
    assert kept.shape[0] >= 3 and np.isfinite(kept[:, :3]).all()
    saved = config.load()
    assert (saved.lockin.model, saved.dmm.model) == ("sr830", "k199")
