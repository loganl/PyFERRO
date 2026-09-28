import errno
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pytest

from ferro import config, sessionlog
from ferro.acquisition import Acquisition
from ferro.analysis import DirectionTracker
from ferro.datafile import COLUMNS, DataWriter, check_writable, unique_path
from ferro.instruments.cnd3 import CND3, PIDError, PIDSensorError, decode_temperature
from ferro.instruments.hp34401a import celsius_to_pt100, pt100_to_celsius
from ferro.instruments.keithley199 import Keithley199
from ferro.instruments.lockin5301a import Lockin5301A
from ferro.instruments.lockin5302 import (Lockin5302, checked_index, counts_to_volts,
                                          parse_ints)
from ferro.instruments.simulated import (Sim5301ATransport, SimLockinTransport,
                                        SimModbusInstrument, SimulatedSample)
from ferro.instruments.sr830 import SR830, parse_floats
from ferro.transports import (TERMINATIONS, TransportError, VisaTransport, describe_status,
                              drain_replies, probe_terminations)


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


# --- SR830 ---------------------------------------------------------------------
class FakeSR830Transport:
    """The SR830's replies (manual ch. 5), with a status byte that latches like the real one."""

    def __init__(self, sens="17", oexp="0.00,0", snap="1.234560e-03,-5.678900e-04,1.35e-03,-24.71"):
        self.writes = []
        self.replies = {"*IDN?": "Stanford_Research_Systems,SR830,s/n12345,ver1.07",
                        "SNAP?1,2,3,4": snap, "SENS?": sens, "OFLT?": "9",
                        "FREQ?": "1000.00", "OEXP?1": oexp, "OEXP?2": "0.00,0", "OEXP?3": "0.00,0"}
        self.lias = 0

    def write(self, cmd):
        self.writes.append(cmd)
        if cmd == "*CLS":
            self.lias = 0

    def query(self, cmd):
        if cmd == "LIAS?":
            value, self.lias = self.lias, 0  # reading clears every bit (manual 5-23)
            return str(value)
        return self.replies[cmd]

    def close(self):
        pass


def test_sr830_against_fake_protocol():
    transport = FakeSR830Transport()
    li = SR830(transport)
    assert transport.writes == ["OUTX1", "*CLS"]  # answers to GPIB; stale status cleared
    assert "SR830" in li.check()
    r = li.read()
    assert (r.x_v, r.y_v) == (pytest.approx(1.23456e-3), pytest.approx(-5.6789e-4))
    assert r.sensitivity == "1 mV" and r.full_scale_v == pytest.approx(1e-3)
    assert r.percent_fs == pytest.approx(123.456)
    assert not r.overloaded and not r.expand
    s = li.settings()
    assert (s["sensitivity"], s["time_constant"], s["expand"]) == ("1 mV", "300 ms", False)


def test_sr830_reading_has_the_5302_reading_fields():
    """Acquisition and the window use these; every lock-in's reading must have them."""
    r = SR830(FakeSR830Transport()).read()
    for name in ("x_v", "y_v", "r_v", "theta_deg", "sen_index", "sensitivity", "expand",
                 "full_scale_v", "percent_fs", "overloaded", "EXPAND_NAME"):
        assert hasattr(r, name), name


def test_sr830_overload_between_samples_is_caught_once():
    transport = FakeSR830Transport()
    li = SR830(transport)
    transport.lias = 1 << 2  # output overload latched since the last sample
    assert li.read().overloaded
    assert not li.read().overloaded  # the read cleared it; nothing new happened


def test_sr830_offset_or_expand_is_reported():
    li = SR830(FakeSR830Transport(oexp="50.00,1"))
    assert li.read().expand
    s = li.settings()
    assert (s["x_offset_percent"], s["x_expand"], s["expand"]) == (50.0, 10, True)


def test_sr830_out_of_step_sensitivity_is_refused_not_indexed():
    with pytest.raises(TransportError, match="out of step"):
        SR830(FakeSR830Transport(sens="1000")).read()


def test_sr830_parse_floats():
    assert parse_floats("+1.5,-2.0e-3, .25", 3) == [1.5, -2.0e-3, 0.25]
    with pytest.raises(TransportError):
        parse_floats("1.0", 2)


# --- Keithley 199 ---------------------------------------------------------------
class FakeKeithley199Transport:
    """The 199's device-dependent commands (manual 3.9), reading ohms with a prefix."""

    def __init__(self, reading="NOHM+1.100000E+2", status="199110020000000000410600000000000"):
        self.writes = []
        self.reading = reading
        self.status = status

    def write(self, cmd):
        self.writes.append(cmd)

    def query(self, cmd):
        return {"U0X": self.status, "X": self.reading}[cmd]

    def close(self):
        pass


def test_keithley199_against_fake_protocol():
    transport = FakeKeithley199Transport()
    dmm = Keithley199(transport, "pt100", 100.0)
    assert transport.writes == ["F2R0T0B0G0X"]  # ohms, set explicitly after the device clear
    assert dmm.check().startswith("199")
    assert dmm.read_raw() == pytest.approx(110.0)
    assert dmm.read_celsius() == pytest.approx(pt100_to_celsius(110.0))


def test_keithley199_overflow_is_an_error_not_a_reading():
    """Without the prefix an overflow is all 9s - a large, plausible-looking number."""
    dmm = Keithley199(FakeKeithley199Transport(reading="OOHM+9.999999E+9"))
    with pytest.raises(TransportError, match="overflow"):
        dmm.read_raw()


def test_keithley199_notices_the_front_panel_function_changed():
    dmm = Keithley199(FakeKeithley199Transport(reading="NDCV+1.100000E+0"))
    with pytest.raises(TransportError, match="DCV"):
        dmm.read_raw()


@pytest.mark.parametrize("reading", ["ERROR", "", "+1.100000E+2"])
def test_keithley199_rejects_garbage(reading):
    with pytest.raises(TransportError):
        Keithley199(FakeKeithley199Transport(reading=reading)).read_raw()


def test_keithley199_check_rejects_a_non_199_status_word():
    with pytest.raises(TransportError):
        Keithley199(FakeKeithley199Transport(status="NOT A 199")).check()


def test_keithley199_has_no_celsius_mode():
    with pytest.raises(TransportError, match="temperature"):
        Keithley199(FakeKeithley199Transport(), mode="celsius")


# --- 5301A (unverified -- see lockin5301a.py) -------------------------------------
def test_5301a_warns_that_it_is_unverified():
    with pytest.warns(UserWarning, match="unverified"):
        Lockin5301A(Sim5301ATransport(SimulatedSample()))


def test_5301a_reuses_the_5302_protocol():
    with pytest.warns(UserWarning):
        li = Lockin5301A(Sim5301ATransport(SimulatedSample()))
    assert "5301" in li.check()
    assert li.read().sen_index == 17


def test_5301a_check_rejects_an_id_that_does_not_say_5301():
    with pytest.warns(UserWarning):
        li = Lockin5301A(SimLockinTransport(SimulatedSample()))  # answers ID "5302"
    with pytest.raises(TransportError):
        li.check()


def test_5301a_out_of_range_does_not_claim_to_know_why():
    t = Sim5301ATransport(SimulatedSample())
    t.sen = 25
    with pytest.warns(UserWarning):
        li = Lockin5301A(t)
    with pytest.raises(TransportError, match="table differs"):
        li.sensitivity_index()


# --- all models, through the app's own factories --------------------------------
@pytest.mark.parametrize("model", ["5302", "sr830", "5301a"])
def test_every_lockin_model_opens_and_reads_in_simulation(model):
    from ferro import acquisition

    cfg = config.AppConfig(simulate=True)
    cfg.lockin.model = model
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        li = acquisition.open_lockin(cfg)
    li.check()
    r = li.read()
    assert math.isfinite(r.x_v) and r.sensitivity in li.SENSITIVITY_LABELS
    s = li.settings()
    assert {"sensitivity", "time_constant", "frequency_hz", "expand"} <= set(s)
    assert s["time_constant"] == li.TIME_CONSTANT_LABELS[li.time_constant_index()]


@pytest.mark.parametrize("model", ["34401a", "k199"])
def test_every_dmm_model_opens_and_reads_in_simulation(model):
    from ferro import acquisition

    cfg = config.AppConfig(simulate=True)
    cfg.dmm.model = model
    dmm = acquisition.open_dmm(cfg)
    dmm.check()
    assert 20 < dmm.read_celsius() < 160


def test_unknown_models_are_reported_not_crashed_on():
    from ferro import acquisition

    cfg = config.AppConfig(simulate=True)
    cfg.lockin.model, cfg.dmm.model = "7265", "hp3458"
    with pytest.raises(TransportError, match="7265"):
        acquisition.open_lockin(cfg)
    with pytest.raises(TransportError, match="hp3458"):
        acquisition.open_dmm(cfg)


def test_sr830_is_opened_with_its_own_terminators_and_no_5302_delays(monkeypatch):
    from ferro import acquisition

    built = []

    class FakeVisa(FakeSR830Transport):
        def __init__(self, resource, **kw):
            super().__init__()
            self.kw = kw
            built.append(self)

    monkeypatch.setattr(acquisition, "VisaTransport", FakeVisa)
    cfg = config.AppConfig()
    cfg.lockin.model = "sr830"
    li = acquisition.open_lockin(cfg)
    assert isinstance(li, SR830) and len(built) == 1
    kw = built[0].kw
    assert (kw["write_termination"], kw["read_termination"]) == ("\n", "\n")
    assert "reply_delay_s" not in kw and "gap_s" not in kw


def test_5301a_is_opened_like_the_5302(monkeypatch):
    """Same terminator search and reply delay: that delay is what made the 5302 work."""
    from ferro import acquisition

    built = []

    class FakeVisa:
        def __init__(self, resource, **kw):
            self.kw = kw
            built.append(self)

        def query(self, cmd):
            return "5301A"

        def set_timeout(self, timeout_s):
            pass

        def close(self):
            pass

    monkeypatch.setattr(acquisition, "VisaTransport", FakeVisa)
    monkeypatch.setattr(acquisition, "_DETECTED_TERMINATIONS", {})
    cfg = config.AppConfig()
    cfg.lockin.model = "5301a"
    with pytest.warns(UserWarning):
        li = acquisition.open_lockin(cfg)
    assert isinstance(li, Lockin5301A)
    assert all(t.kw["reply_delay_s"] == acquisition.LOCKIN_GAP_S for t in built)


def test_the_5301a_warning_reaches_the_log_and_the_data_file(tmp_path):
    logs = []
    cfg = config.AppConfig(simulate=True)
    cfg.lockin.model = "5301a"
    cfg.run.output_dir = str(tmp_path)
    cfg.run.interval_s = 0.1
    cfg.run.sample = "unverified"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append((lvl, m)))
        acq.start(record=True)
        time.sleep(0.5)
        acq.stop()
    assert any(lvl == "warning" and "unverified" in m for lvl, m in logs)
    text = next(tmp_path.glob("unverified_*.txt")).read_text()
    assert "# lockin_warning:" in text and "# lockin_model: 5301A" in text


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


def test_check_writable_accepts_a_normal_folder(tmp_path):
    target = tmp_path / "new" / "FerroData"
    check_writable(target)
    assert target.is_dir()
    assert not list(target.iterdir()), "the probe file must be cleaned up"


def test_check_writable_survives_a_folder_that_forbids_deleting(tmp_path, monkeypatch):
    """Writing works but unlink fails: recording must still be allowed."""
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError(1, "Operation not permitted")))
    check_writable(tmp_path)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows ignores the read-only attribute on a directory; access is by ACL",
)
def test_check_writable_reports_a_read_only_folder(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(OSError, match="Cannot write into"):
            check_writable(locked)
    finally:
        locked.chmod(0o700)


def test_check_writable_reports_a_refused_write(tmp_path, monkeypatch):
    """The same path as above, on every platform: the folder exists, the write is denied."""
    def refuse(self, *a, **k):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(Path, "write_text", refuse)
    with pytest.raises(OSError, match="Cannot write into") as caught:
        check_writable(tmp_path)
    assert "read-only" in str(caught.value) or "Privacy" in str(caught.value)


def test_default_data_dir_avoids_documents_on_macos(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert "Documents" not in config.default_data_dir()
    monkeypatch.setattr(config.sys, "platform", "win32")
    assert config.default_data_dir().endswith("FerroData")
    assert "Documents" in config.default_data_dir()


def test_session_log_writes_and_keeps_the_newest_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sessionlog, "_current", None)
    folder = tmp_path / "logs"
    for i in range(sessionlog.KEEP_FILES + 5):  # older logs must be pruned
        (folder).mkdir(parents=True, exist_ok=True)
        (folder / f"pyferro_2020010{i % 9}_00000{i % 9}.log").write_text("old")
    log = sessionlog.start(folder)
    log.write("warning", "CND3: No valid reply\nsecond line")
    assert "CND3: No valid reply" in log.path.read_text()
    assert "WARNING" in log.path.read_text()
    assert len(list(folder.glob("pyferro_*.log"))) <= sessionlog.KEEP_FILES + 1
    assert sessionlog.path() == str(log.path)


def test_session_log_never_raises_when_it_cannot_be_written(tmp_path, monkeypatch):
    monkeypatch.setattr(sessionlog, "_current", None)
    blocker = tmp_path / "file.txt"
    blocker.write_text("not a folder")
    log = sessionlog.start(blocker / "logs")  # mkdir under a file fails
    assert log.path is None
    log.write("error", "must not raise")      # no-ops
    sessionlog.write("error", "also fine")
    assert log.copy_to(tmp_path / "copy.log") is False
    assert sessionlog.path() is None


def test_recording_copies_the_session_log_next_to_the_data(tmp_path, monkeypatch):
    monkeypatch.setattr(sessionlog, "_current", None)
    sessionlog.start(tmp_path / "logs")
    sessionlog.write("info", "Test controller: CND3 V1.00 before recording started")

    cfg = config.AppConfig(simulate=True)
    cfg.run.output_dir = str(tmp_path / "data")
    cfg.run.interval_s = 0.1
    cfg.run.sample = "log test"
    acq = Acquisition(cfg)
    acq.start(record=True)
    time.sleep(0.6)
    acq.stop()

    data = next((tmp_path / "data").glob("log_test_*.txt"))
    sidecar = data.with_suffix(".log")
    assert sidecar.exists(), "the session log should be copied beside the data file"
    text = sidecar.read_text()
    assert "before recording started" in text
    # The core must log to the session file itself, not only through the GUI.
    assert "Acquisition started" in text and "Recording to" in text
    assert f"# session_log: {sessionlog.path()}" in data.read_text()


def test_annotations_land_in_the_data_file(tmp_path):
    cfg = config.AppConfig(simulate=True)
    cfg.run.output_dir = str(tmp_path)
    cfg.run.interval_s = 0.1
    cfg.run.sample = "note"
    acq = Acquisition(cfg)
    acq.start(record=True)
    time.sleep(0.4)
    acq.annotate("Reading interval changed to 5 s")
    time.sleep(0.3)
    acq.stop()

    path = next(tmp_path.glob("note_*.txt"))
    text = path.read_text()
    assert "# " in text and "Reading interval changed to 5 s" in text
    assert np.loadtxt(path).shape[0] >= 3, "comment lines must not break numeric loading"
    assert "# connection_lockin: Lock-in: 5302 SIMULATED" in text


def test_connections_name_the_actual_ports():
    cfg = config.AppConfig()
    cfg.lockin.resource = "GPIB0::12::INSTR"
    cfg.pid.port = "COM7"
    cfg.dmm.enabled = True
    conn = Acquisition(cfg).connections()
    assert "GPIB0::12::INSTR" in conn["lockin"]
    assert "COM7" in conn["pid"] and "ASCII" in conn["pid"] and "9600 7E1" in conn["pid"]
    assert "GPIB0::24::INSTR" in conn["dmm"]


def test_lockin_range_and_overload_changes_are_announced():
    from ferro.instruments.lockin5302 import LockinReading

    logs = []
    acq = Acquisition(config.AppConfig(simulate=True), on_log=lambda lvl, m: logs.append(m))

    def reading(counts, sen, expand=False):
        return LockinReading(counts, 0, sen, expand, 0.0, 0.0)

    acq._watch_lockin(reading(5000, 17))          # first sample: nothing to report
    assert logs == []
    acq._watch_lockin(reading(5000, 19))          # range changed
    acq._watch_lockin(reading(12500, 19))         # now overloaded
    acq._watch_lockin(reading(4000, 19))          # cleared
    assert any("sensitivity changed to 200 mV" in m for m in logs)
    assert any("OVERLOAD" in m for m in logs)
    assert any("overload cleared" in m for m in logs)


def test_recovery_after_a_gap_is_announced():
    logs = []
    acq = Acquisition(config.AppConfig(simulate=True), on_log=lambda lvl, m: logs.append(m))
    slot = acq.slots["pid"]
    slot.device = object()
    slot.state = "error"
    slot.failing_since = time.monotonic() - 12
    acq._read("pid", lambda device: "a reading")
    assert any("answering again after 12 s" in m for m in logs), logs


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


def test_simulation_is_never_restored_from_the_settings_file():
    """--simulate once must not leave every later run quietly simulated."""
    saved = config.AppConfig(simulate=True).to_dict()
    assert "simulate" not in saved
    # even a settings file written by an older version must not switch it back on
    assert config.AppConfig.from_dict({"simulate": True}).simulate is False


# --- GPIB terminator probing -----------------------------------------------------------
def test_probe_terminations_keeps_the_first_that_answers():
    opened, closed = [], []

    class FakeTransport:
        def __init__(self, write_t, read_t):
            self.pair = (write_t, read_t)

        def close(self):
            closed.append(self.pair)

    def open_one(write_t, read_t):
        opened.append((write_t, read_t))
        return FakeTransport(write_t, read_t)

    works = ("\r\n", "\r")  # input CR LF, output CR: the 5302 selects them separately
    at = TERMINATIONS.index(works)

    def verify(t):
        if t.pair != works:
            raise TransportError("timeout")

    transport, label = probe_terminations(open_one, verify, name="Lock-in")
    assert transport.pair == works
    assert label == "write CRLF, read CR"
    assert opened == TERMINATIONS[:at + 1], "should stop at the first that answers"
    assert closed == TERMINATIONS[:at], "every rejected transport must be closed"


def test_probe_terminations_reports_everything_it_tried():
    def open_one(write_t, read_t):
        raise TransportError("VI_ERROR_TMO")

    with pytest.raises(TransportError) as caught:
        probe_terminations(open_one, lambda t: None, name="Lock-in GPIB0::12::INSTR")
    message = str(caught.value)
    assert "Lock-in GPIB0::12::INSTR did not answer with any terminator" in message
    assert "powered on" in message  # an unpowered device stalls the whole bus
    assert message.count("VI_ERROR_TMO") == len(TERMINATIONS)


def test_status_byte_explains_a_rejected_command():
    assert describe_status(0b00000001) == ""  # command complete, nothing wrong
    assert "invalid command" in describe_status(0b00000010)
    assert "command parameter error" in describe_status(0b00000100)
    faults = describe_status(0b00011000)
    assert "reference unlock" in faults and "overload" in faults
    assert describe_status(0b10000000) == ""  # data available is not a fault


def test_probe_terminations_gives_up_when_asked_to_stop():
    """A silent instrument costs a timeout per pair; Stop must not wait for all of them."""
    opened = []

    class FakeTransport:
        def close(self):
            pass

    def open_one(write_t, read_t):
        opened.append((write_t, read_t))
        return FakeTransport()

    def verify(t):
        raise TransportError("timeout")

    stopping = iter([False, False, True, True, True, True, True, True, True])
    with pytest.raises(TransportError, match="stopping"):
        probe_terminations(open_one, verify, name="Lock-in", should_stop=lambda: next(stopping))
    assert len(opened) == 2, "should abort at the third pair, not work through all eight"


def test_probe_terminations_records_the_pair_that_worked():
    class FakeTransport:
        def close(self):
            pass

    transport, _ = probe_terminations(lambda w, r: FakeTransport(), lambda t: None, name="Lock-in")
    assert transport.terminators == TERMINATIONS[0]
    assert transport.detected == "write CR, read CR"


def test_falling_behind_names_the_instrument_that_is_holding_things_up():
    acq = Acquisition(config.AppConfig(simulate=True), on_log=lambda lvl, m: None)
    acq._durations = {"pid": 0.05, "lockin": 2.10}
    acq.slots["lockin"].state = "error"
    message = acq._behind_message(0.2)
    assert "a reading takes 2.1 s but the interval is 0.2 s" in message
    assert "waiting for the lock-in (not answering)" in message

    # spread evenly across instruments: no one of them is to blame
    acq._durations = {"pid": 0.5, "lockin": 0.5}
    assert "waiting for" not in acq._behind_message(0.2)


def test_a_setting_index_out_of_range_is_refused_not_indexed():
    """5302 replies arriving one command late used to crash with IndexError."""
    for command, size in [("SEN", 22), ("XTC", 19)]:
        assert checked_index(0, size, command) == 0
        assert checked_index(size - 1, size, command) == size - 1
        with pytest.raises(TransportError, match="out of step"):
            checked_index(5302, size, command)  # an ID reply read as a setting
        with pytest.raises(TransportError, match=f"{command} answered -1"):
            checked_index(-1, size, command)


def test_drain_replies_clears_a_late_reply_then_stops():
    """A query that times out can still deliver its reply, one command too late."""
    queued = iter(["5302"])

    def read():
        return next(queued)  # StopIteration once the buffer is empty

    assert drain_replies(read) == 1


def test_drain_replies_stops_rather_than_looping_forever():
    assert drain_replies(lambda: "junk", limit=3) == 3


def test_a_marginal_link_is_retried_before_it_is_reported():
    """The rig's GPIB link drops the odd exchange; one lost byte is not a failed run."""
    import threading

    class FlakyInstrument:
        def __init__(self, failures):
            self.failures, self.calls, self.timeout, self.delays = failures, 0, 2000, []

        def query(self, cmd, delay=None):
            self.calls += 1
            self.delays.append(delay)
            if self.calls <= self.failures:
                raise OSError("VI_ERROR_TMO")
            return "5302\r"

        def read(self):
            raise OSError("nothing queued")

        def read_stb(self):
            return 0

    def transport(failures):
        t = VisaTransport.__new__(VisaTransport)
        t.name, t._lock, t._gap = "GPIB0::12::INSTR", threading.Lock(), 0.0
        t._reply_delay = 0.05
        t.retries, t.retries_used = 2, 0
        t._inst = FlakyInstrument(failures)
        return t

    t = transport(2)  # fails twice, succeeds on the third attempt
    assert t.query("ID") == "5302"
    assert t.retries_used == 2
    assert set(t._inst.delays) == {0.05}, "the reply is asked for only after the 5302 has parsed the query"

    t = transport(3)  # more failures than retries: report it
    with pytest.raises(TransportError, match="query 'ID' failed"):
        t.query("ID")
    assert t._inst.calls == 3, "should not keep trying forever"


# --- lock-in scaling and connection details --------------------------------------------
class ScriptedTransport:
    """Answers each command from a table; a value that is an exception is raised."""

    def __init__(self, replies):
        self.replies = replies

    def query(self, cmd):
        reply = self.replies[cmd]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def write(self, cmd):
        pass

    def close(self):
        pass


def test_expand_scales_x_only():
    """Manual sections 4 and 9: Expand multiplies the x channel's gain by 10; y is untouched."""
    li = Lockin5302(ScriptedTransport({"SEN": "15", "EX": "1", "XY": "5000,5000"}))
    r = li.read()  # 10 mV full scale
    assert r.x_v == pytest.approx(0.5e-3)  # 5000/10000 * 10 mV / 10
    assert r.y_v == pytest.approx(5e-3)  # 5000/10000 * 10 mV


def test_a_failed_expand_query_loses_the_sample_rather_than_guessing():
    """Assuming expand off after one dropped exchange would put X out by ten, silently."""
    replies = {"SEN": "15", "EX": TransportError("VI_ERROR_TMO"), "XY": "5000,5000"}
    li = Lockin5302(ScriptedTransport(replies))
    with pytest.raises(TransportError):
        li.read()
    replies["EX"] = "1"  # the link recovers: expand must be asked about again
    assert li.read().expand is True


def test_simulated_expand_matches_the_instrument():
    t = SimLockinTransport(SimulatedSample())
    t.sen, t.expand = 17, 1
    li = Lockin5302(t)
    r = li.read()
    assert abs(r.y_v) < 0.05 * 1.2, "y must stay on the unexpanded 50 mV scale"


def test_the_terminator_probe_runs_without_retries(monkeypatch):
    """Retries during the probe multiply the cost of every wrong pair and delay Stop."""
    from ferro import acquisition

    built = []

    class FakeVisa:
        def __init__(self, resource, **kw):
            self.kw, self.retries = kw, kw["retries"]
            built.append(self)

        def query(self, cmd):
            return "5302"

        def set_timeout(self, timeout_s):
            self.timeout_s = timeout_s

        def close(self):
            pass

    monkeypatch.setattr(acquisition, "VisaTransport", FakeVisa)
    monkeypatch.setattr(acquisition, "_DETECTED_TERMINATIONS", {})
    cfg = config.AppConfig()
    li = acquisition.open_lockin(cfg)
    assert all(t.kw["retries"] == 0 for t in built)
    assert all(t.kw["reply_delay_s"] == acquisition.LOCKIN_GAP_S for t in built), \
        "the lock-in must be given time to parse a query before it is asked to answer"
    assert li.t.retries == acquisition.LOCKIN_RETRIES, "retries switch on once connected"
    assert li.t.timeout_s == cfg.lockin.timeout_s
