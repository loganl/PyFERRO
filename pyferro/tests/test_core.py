import json
import math
import time

import numpy as np
import pytest

from ferro import config
from ferro.acquisition import Acquisition
from ferro.analysis import DirectionTracker
from ferro.datafile import COLUMNS, DataWriter, unique_path
from ferro.instruments.cnd3 import CND3, PIDError, PIDSensorError, decode_temperature
from ferro.instruments.hp34401a import celsius_to_pt100, pt100_to_celsius
from ferro.instruments.lockin5302 import Lockin5302, counts_to_volts, parse_ints
from ferro.instruments.simulated import SimLockinTransport, SimModbusInstrument, SimulatedSample
from ferro.transports import TransportError


# --- lock-in -----------------------------------------------------------------
def test_counts_to_volts_uses_manual_scaling():
    assert counts_to_volts(10000, 17) == pytest.approx(50e-3)  # SEN 17 = 50 mV
    assert counts_to_volts(-5000, 0) == pytest.approx(-50e-9)  # SEN 0 = 100 nV
    assert counts_to_volts(10000, 21, expand=True) == pytest.approx(0.1)
    with pytest.raises(TransportError):
        counts_to_volts(1, 22)


@pytest.mark.parametrize("text", ["123,-456", "123 -456", "  123\t-456\r\n", "+123;-456"])
def test_parse_xy_any_delimiter(text):
    assert parse_ints(text, 2) == [123, -456]


def test_parse_rejects_garbage():
    with pytest.raises(TransportError):
        parse_ints("?", 2)


def test_lockin_against_simulated_protocol():
    li = Lockin5302(SimLockinTransport(SimulatedSample()))
    assert li.check() == "5302"
    r = li.read()
    assert r.sen_index == 17
    assert r.x_v == pytest.approx(r.x_counts / 10000 * 50e-3)
    assert li.settings()["time_constant"] == "200 ms"
    assert li.frequency_hz() == pytest.approx(25000.0)


class SplitReplyTransport:
    """GPIB-style compound reply: XY answers '5000' then '-2500' as separate reads."""

    def __init__(self):
        self.pending = []

    def query(self, cmd):
        if cmd == "XY":
            self.pending = ["-2500"]
            return "5000"
        return {"SEN": "15", "EX": "0"}[cmd]

    def read(self):
        return self.pending.pop(0)

    def close(self):
        pass


def test_lockin_xy_split_over_two_reads():
    r = Lockin5302(SplitReplyTransport()).read()
    assert (r.x_counts, r.y_counts) == (5000, -2500)
    assert r.x_v == pytest.approx(5e-3)


# --- CND3 ----------------------------------------------------------------------
def test_decode_temperature():
    assert decode_temperature(0x0190) == pytest.approx(40.0)
    assert decode_temperature(0x05DC) == pytest.approx(150.0)
    assert decode_temperature(0xFC18) == pytest.approx(-100.0)
    with pytest.raises(PIDSensorError, match="not connected"):
        decode_temperature(0x8003)


def test_cnd3_rejects_unsupported_framing():
    with pytest.raises(PIDError):
        CND3("X", bytesize=8, parity="E", stopbits=2, instrument=object())


def test_cnd3_simulated_reading():
    pid = CND3("SIM", instrument=SimModbusInstrument(SimulatedSample()))
    r = pid.read()
    assert 20 < r.pv_c < 160
    assert pid.status()["run_state"] == "RUN"
    assert pid.version() == "V1.00"


# --- Pt100 ---------------------------------------------------------------------
def test_pt100_iec60751_points():
    assert pt100_to_celsius(100.0) == pytest.approx(0.0, abs=1e-9)
    assert pt100_to_celsius(138.5055) == pytest.approx(100.0, abs=1e-3)
    assert pt100_to_celsius(celsius_to_pt100(153.4)) == pytest.approx(153.4)


# --- data files ----------------------------------------------------------------
def test_datafile_roundtrip(tmp_path):
    path = unique_path(tmp_path, "BTO 1V/37kHz")
    assert "/" not in path.name and " " not in path.name
    w = DataWriter(path, {"sample": "BTO"})
    w.write({"T_C": 27.1, "X_V": 1e-4, "Y_V": -2e-3, "time_s": 0.0, "flags": 0, "direction": 1, "segment": 0})
    w.write({"T_C": float("nan"), "X_V": 1e-4, "Y_V": -2e-3, "time_s": 1.0, "flags": 4})
    w.close()
    data = np.loadtxt(path)
    assert data.shape == (2, len(COLUMNS))
    assert data[0, 0] == pytest.approx(27.1)
    assert math.isnan(data[1, 0])
    assert "# sample: BTO" in path.read_text()
    assert unique_path(tmp_path, "BTO 1V/37kHz", when=None) != path or not path.exists()


def test_datafile_never_overwrites(tmp_path):
    path = tmp_path / "a.txt"
    DataWriter(path, {}).close()
    with pytest.raises(FileExistsError):
        DataWriter(path, {})


def test_legacy_format_matches_labview(tmp_path):
    path = tmp_path / "legacy.txt"
    w = DataWriter(path, {"sample": "VO2"}, legacy=True)
    w.write({"T_C": 33.535, "X_V": 2.722e-2, "Y_V": 3.1e-4, "time_s": 5.0})
    w.close()
    assert path.read_text() == "3.35350e+01\t2.72200e-02\t3.10000e-04\n"
    assert json.loads(path.with_suffix(".json").read_text())["sample"] == "VO2"


# --- direction tracking ----------------------------------------------------------
def test_direction_tracker_heating_then_cooling():
    tr = DirectionTracker(window_s=30, threshold_c_per_min=0.5)
    t = 0.0
    for _ in range(120):  # 2 min heating at 3 degC/min
        tr.update(t, 30 + 0.05 * t)
        t += 1
    assert tr.direction == 1
    peak = 30 + 0.05 * t
    for _ in range(120):
        tr.update(t, peak - 0.05 * (t - 120))
        t += 1
    assert tr.direction == -1
    assert tr.segment == 1


# --- settings ----------------------------------------------------------------------
def test_config_roundtrip_and_bad_values(tmp_path, monkeypatch):
    monkeypatch.setenv("FERRO_CONFIG_DIR", str(tmp_path))
    cfg = config.AppConfig()
    cfg.pid.port = "COM7"
    cfg.run.interval_s = 0.5
    config.save(cfg)
    assert config.load().pid.port == "COM7"
    bad = config.AppConfig.from_dict({"run": {"interval_s": "fast"}, "pid": {"address": "3"}})
    assert bad.run.interval_s == 1.0 and bad.pid.address == 3
    (tmp_path / "settings.json").write_text("{not json")
    assert config.load().pid.port == ""


# --- acquisition end-to-end (simulated) ------------------------------------------------
def test_simulated_acquisition_records(tmp_path):
    cfg = config.AppConfig(simulate=True)
    cfg.run.output_dir = str(tmp_path)
    cfg.run.interval_s = 0.1
    cfg.run.sample = "sim"
    rows, logs = [], []
    acq = Acquisition(cfg, on_sample=rows.append, on_log=lambda lvl, m: logs.append((lvl, m)))
    acq.start(record=True)
    time.sleep(1.0)
    acq.set_recording(False)
    time.sleep(0.3)
    acq.stop()
    assert not acq.running
    assert len(rows) >= 5
    assert all(r["flags"] == 0 for r in rows), logs
    files = list(tmp_path.glob("sim_*.txt"))
    assert len(files) == 1
    data = np.loadtxt(files[0])
    assert data.shape[0] >= 4
    text = files[0].read_text()
    assert "lockin_sensitivity: 50 mV" in text and "controller_firmware: V1.00" in text


def test_acquisition_survives_missing_instruments(tmp_path):
    cfg = config.AppConfig()
    cfg.lockin.resource = "GPIB0::99::INSTR"
    cfg.pid.port = ""
    cfg.run.interval_s = 0.1
    cfg.run.output_dir = str(tmp_path)
    rows, statuses = [], []
    acq = Acquisition(cfg, on_sample=rows.append, on_status=lambda *a: statuses.append(a))
    acq.start(record=True)
    time.sleep(0.6)
    acq.stop()
    assert rows and all(math.isnan(r["X_V"]) and math.isnan(r["T_C"]) for r in rows)
    assert {s[0] for s in statuses} == {"lockin", "pid"}
    assert all(s[1] == "error" for s in statuses)
