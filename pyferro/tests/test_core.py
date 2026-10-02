import errno
import json
import math
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pytest

from ferro import acquisition, config, sessionlog
from ferro.acquisition import Acquisition
from ferro.analysis import DirectionTracker
from ferro.datafile import COLUMNS, FLAG_TEMP_DISCARDED, DataWriter, check_writable, unique_path
from ferro.instruments.cnd3 import CND3, PIDError, PIDSensorError, decode_temperature
from ferro.instruments.hp34401a import celsius_to_pt100, pt100_to_celsius
from ferro.instruments.keithley199 import Keithley199
from ferro.instruments.lockin5302 import (Lockin5302, checked_index, counts_to_volts,
                                          parse_ints)
from ferro.instruments.simulated import SimLockinTransport, SimModbusInstrument, SimulatedSample
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


def test_5302_front_panel_setup_is_read_back():
    """Manual ch. 9: IE, OA, OF, DR, FLT, PREAMP and P answer their values when sent bare."""
    s = Lockin5302(SimLockinTransport(SimulatedSample())).settings()
    assert (s["reference_mode"], s["dynamic_reserve"], s["filter"], s["signal_input"]) == \
        ("INT", "HI STAB", "FLAT", "PREAMP")
    assert s["oscillator_v"] == pytest.approx(0.500)  # OA 5000 1: 5000 steps of 0.1 mV
    assert s["oscillator_hz"] == pytest.approx(25000)  # OF 2500 7: 25 kHz
    assert s["phase_deg"] == pytest.approx(0.0)


@pytest.mark.parametrize("reply, volts", [("500 0", 5e-3), ("5000 1", 0.5), ("250 2", 0.25)])
def test_5302_oscillator_level_ranges(reply, volts):
    li = Lockin5302(ScriptedTransport({"OA": reply}))
    assert li.oscillator_v() == pytest.approx(volts)


@pytest.mark.parametrize("reply, hz", [("1000 0", 1e-3), ("2500 7", 25e3), ("10000 8", 1e6)])
def test_5302_oscillator_frequency_ranges(reply, hz):
    assert Lockin5302(ScriptedTransport({"OF": reply})).oscillator_hz() == pytest.approx(hz)


def test_5302_phase_from_quadrant_and_millidegrees():
    assert Lockin5302(ScriptedTransport({"P": "1 5000"})).phase_deg() == pytest.approx(95.0)
    assert Lockin5302(ScriptedTransport({"P": "3 95000"})).phase_deg() == pytest.approx(5.0)


def test_a_setup_value_that_does_not_answer_is_left_unread_not_fatal():
    replies = {"SEN": "21", "XTC": "8", "EX": "0", "FRQ": "25000000", "IE": "2",
               "OA": TransportError("VI_ERROR_TMO"), "OF": "2500 7", "DR": "1", "FLT": "3",
               "PREAMP": "0", "P": "0 0"}
    s = Lockin5302(ScriptedTransport(replies)).settings()
    assert s["oscillator_v"] is None and s["reference_mode"] == "EXT" and s["filter"] == "BAND-PASS"


def test_setup_check_finds_what_was_wrong_on_the_rig():
    """The 5302 as read on 2026-09-29, against the lab manual's capacitance table."""
    from ferro.instruments.lockin5302 import check_setup

    rig = {"reference_mode": "INT", "oscillator_hz": 25000.0, "oscillator_v": 2.0,
           "sensitivity": "500 mV", "expand": True, "time_constant": "F 100 µs",
           "filter": "LOW-PASS", "dynamic_reserve": "MIN", "signal_input": "DIRECT"}
    rows = {r.name: r for r in check_setup(rig)}
    wrong = {name for name, r in rows.items() if r.ok is False}
    assert wrong == {"Oscillator level", "Sensitivity", "Expand", "Time constant", "Filter",
                     "Dynamic reserve", "Signal input"}
    assert rows["Oscillator level"].now == "2.000 V" and rows["Oscillator level"].wanted == "0.500 V"
    assert rows["Time constant"].wanted == "500 ms or 200 ms"
    assert "FUNCT" in rows["Expand"].how
    rig["oscillator_v"] = None
    assert {r.name: r for r in check_setup(rig)}["Oscillator level"].ok is None  # unread, not wrong


def test_setup_check_passes_a_correctly_set_lockin():
    from ferro.instruments.lockin5302 import check_setup

    good = {"reference_mode": "INT", "oscillator_hz": 25010.0, "oscillator_v": 0.5005,
            "sensitivity": "1 V", "expand": False, "time_constant": "200 ms", "filter": "FLAT",
            "dynamic_reserve": "HI STAB", "signal_input": "PREAMP"}
    assert all(r.ok for r in check_setup(good))


def test_cnd3_reports_its_output_and_setpoint_configuration():
    from ferro.gui.setup_panel import describe_controller

    s = CND3("SIM", instrument=SimModbusInstrument(SimulatedSample())).status()
    assert (s["output2_percent"], s["output1_max_percent"], s["sv_mode"]) == (0.0, 100.0, "constant")
    text = describe_controller({**s, "sv_mode": "slope", "sv_slope_c_per_min": 2.5,
                                "output1_max_percent": 40.0})
    assert "ramps at 2.5 °C/min" in text and "LIMITED to 40.0%" in text


def test_recording_header_says_how_the_lockin_differs_from_the_manual(tmp_path):
    cfg = config.AppConfig(simulate=True)  # the simulated 5302 sits at 50 mV, not 1 V
    logs = []
    acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append((lvl, m)))
    meta = acq._metadata()
    assert "Sensitivity 50 mV (manual: 1 V)" in meta["lockin_setup_check"]
    assert any(lvl == "warning" and "differ from the lab manual" in m for lvl, m in logs)
    for slot in acq.slots.values():
        slot.close()


@pytest.mark.parametrize("volts, command", [(1.0, "OA 1000 2"), (0.1, "OA 1000 1"),
                                            (0.01, "OA 1000 0"), (5.0, "OA 5000 2"),
                                            (0.005, "OA 500 0"), (0.3, "OA 3000 1")])
def test_5302_oscillator_level_command_round_trips(volts, command):
    from ferro.instruments.lockin5302 import oscillator_level_command

    assert oscillator_level_command(volts) == command
    reply = command.split(" ", 1)[1]
    assert Lockin5302(ScriptedTransport({"OA": reply})).oscillator_v() == pytest.approx(volts)


@pytest.mark.parametrize("hz, command", [(25000, "OF 2500 7"), (1000, "OF 1000 6"),
                                         (999, "OF 9990 5"), (1e6, "OF 10000 8"),
                                         (0.001, "OF 1000 0")])
def test_5302_oscillator_frequency_command_round_trips(hz, command):
    from ferro.instruments.lockin5302 import oscillator_frequency_command

    assert oscillator_frequency_command(hz) == command
    reply = command.split(" ", 1)[1]
    assert Lockin5302(ScriptedTransport({"OF": reply})).oscillator_hz() == pytest.approx(hz)


def test_5302_apply_sends_the_time_constant_before_the_reserve_and_checks_first():
    """A FAST time constant forces MIN reserve (manual 4.3), undoing a reserve set before it."""
    sent = []

    class Recorder:
        def write(self, cmd):
            sent.append(cmd)

    li = Lockin5302(Recorder())
    li.apply({"dynamic_reserve": "HI STAB", "time_constant_index": 8, "oscillator_v": 1.0})
    assert sent == ["OA 1000 2", "XTC 8", "DR 1"]
    sent.clear()
    with pytest.raises(ValueError):
        li.apply({"time_constant_index": 8, "oscillator_v": 9.0})  # 9 V is out of range
    assert sent == [], "nothing is sent when any value is bad"


def test_lockin_changes_during_a_run_are_sent_and_recorded():
    cfg = config.AppConfig(simulate=True)
    cfg.lockin.model = "5302"
    logs, seen = [], []
    acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append(m), on_lockin=seen.append)
    acq.slots["lockin"].get()
    acq._poll_settings()  # the baseline
    acq.set_lockin({"time_constant_index": 8, "filter": "BAND-PASS"})
    acq._handle_lockin_request()
    acq._poll_settings()  # set_lockin asks for an immediate read-back
    assert any(m.startswith("Lock-in set from PyFERRO: time constant 500 ms (XTC 8)") for m in logs)
    assert "Lock-in time constant changed to 500 ms" in logs
    assert "Lock-in filter changed to BAND-PASS" in logs
    assert seen[-1]["filter"] == "BAND-PASS"
    for slot in acq.slots.values():
        slot.close()


def test_a_front_panel_change_during_a_run_is_recorded():
    cfg = config.AppConfig(simulate=True)
    cfg.lockin.model = "5302"
    logs = []
    acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append(m))
    li = acq.slots["lockin"].get()
    acq._poll_settings()
    li.t.setup["IE"] = "2"  # someone presses REF
    li.t.setup["OA"] = "2000 2"
    acq.refresh_lockin()
    acq._poll_settings()
    assert "Lock-in reference changed to EXT" in logs
    assert "Lock-in oscillator level changed to 2.000 V" in logs
    for slot in acq.slots.values():
        slot.close()


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
                        "FREQ?": "25000.0", "OEXP?1": oexp, "OEXP?2": "0.00,0", "OEXP?3": "0.00,0",
                        # the lab manual's SR830 table (manual 5-4 to 5-8)
                        "FMOD?": "1", "SLVL?": "0.500", "ISRC?": "0", "ICPL?": "0", "IGND?": "0",
                        "ILIN?": "0", "RMOD?": "2", "OFSL?": "1", "DDEF?1": "0,0",
                        "DDEF?2": "0,0", "PHAS?": "-12.34"}
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


def test_sr830_status_that_needed_a_retry_is_flagged_as_overload():
    """Reading LIAS? clears it; if the reply was lost and retried, the bits may be gone."""

    class RetryingTransport(FakeSR830Transport):
        retries_used = 0

        def query(self, cmd):
            if cmd == "LIAS?":
                self.lias = 0  # the lost first attempt cleared the byte
                self.retries_used += 1
            return super().query(cmd)

    li = SR830(RetryingTransport())
    assert li.read().overloaded


def test_sr830_offset_or_expand_is_reported():
    li = SR830(FakeSR830Transport(oexp="50.00,1"))
    assert li.read().expand
    s = li.settings()
    assert (s["x_offset_percent"], s["x_expand"], s["expand"]) == (50.0, 10, True)


def test_sr830_out_of_step_sensitivity_is_refused_not_indexed():
    with pytest.raises(TransportError, match="out of step"):
        SR830(FakeSR830Transport(sens="1000")).read()


def test_sr830_setup_is_read_and_checked_against_the_lab_manual():
    from ferro.instruments.lockin5302 import check_setup

    li = SR830(FakeSR830Transport(sens="26"))
    s = li.settings()
    assert (s["reference_mode"], s["oscillator_hz"], s["oscillator_v"]) == ("INT", 25000.0, 0.5)
    assert (s["signal_input"], s["coupling"], s["grounding"]) == ("A", "AC", "FLOAT")
    assert (s["dynamic_reserve"], s["filter_slope"], s["display"]) == ("LOW NOISE", "12 dB/oct", "X, Y")
    assert s["phase_deg"] == pytest.approx(-12.34)
    rows = check_setup(s, li.SETUP_TABLE)
    assert rows and all(r.ok for r in rows), [r for r in rows if not r.ok]
    s["coupling"] = "DC"
    assert [r.name for r in check_setup(s, li.SETUP_TABLE) if not r.ok] == ["Coupling"]


def test_sr830_unreadable_setting_is_unread_not_a_failure():
    transport = FakeSR830Transport()

    def query(cmd, _q=transport.query):
        if cmd == "ICPL?":
            raise TransportError("timeout")
        return _q(cmd)

    transport.query = query
    assert SR830(transport).settings()["coupling"] is None


def test_sr830_apply_sends_commands_in_a_safe_order():
    transport = FakeSR830Transport()
    li = SR830(transport)
    sent = li.apply({"time_constant_index": 9, "dynamic_reserve": "LOW NOISE",
                     "oscillator_hz": 25000.0, "reference_mode": "INT", "expand": False})
    assert transport.writes[2:] == ["FMOD 1", "FREQ 25000.0000", "RMOD 2", "OFLT 9",
                                    "OEXP 1,0,0;OEXP 2,0,0;OEXP 3,0,0"]
    assert sent[0] == "reference INT (FMOD 1)"


def test_sr830_bad_values_send_nothing():
    transport = FakeSR830Transport()
    li = SR830(transport)
    for bad in ({"oscillator_v": 9.0}, {"expand": True}, {"coupling": "XX"},
                {"oscillator_hz": 200e3}, {"display": "X, θ"}, {"no_such": 1}):
        with pytest.raises(ValueError):
            li.apply({"time_constant_index": 9, **bad})
    assert transport.writes == ["OUTX1", "*CLS"]


def test_sr830_simulation_matches_the_lab_manual_except_sensitivity():
    from ferro import acquisition
    from ferro.instruments.lockin5302 import check_setup

    cfg = config.AppConfig(simulate=True)
    li = acquisition.open_lockin(cfg)
    assert li.MODEL == "SR830", "the SR830 is the default lock-in"
    rows = check_setup(li.settings(), li.SETUP_TABLE)
    assert [r.name for r in rows if not r.ok] == ["Sensitivity"]
    li.apply({"sensitivity_index": 26, "display": "R, θ"})
    s = li.settings()
    assert (s["sensitivity"], s["display"]) == ("1 V", "R, θ")


def test_sr830_changes_during_a_run_are_sent_and_recorded():
    cfg = config.AppConfig(simulate=True)
    logs, seen = [], []
    acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append(m), on_lockin=seen.append)
    li = acq.slots["lockin"].get()
    acq._poll_settings()  # the baseline
    acq.set_lockin({"time_constant_index": 10, "coupling": "DC"})
    acq._handle_lockin_request()
    acq._poll_settings()
    assert any(m.startswith("Lock-in set from PyFERRO: coupling DC (ICPL 1), time constant 1 s")
               for m in logs)
    assert "Lock-in time constant changed to 1 s" in logs
    assert "Lock-in input coupling changed to DC" in logs
    li.t.setup["FMOD"] = "0"  # someone presses Source
    acq.refresh_lockin()
    acq._poll_settings()
    assert "Lock-in reference changed to EXT" in logs
    assert seen[-1]["reference_mode"] == "EXT"
    for slot in acq.slots.values():
        slot.close()


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
    assert transport.writes == ["F2R0T0B0Z0G0X"]  # ohms, zero off, set after the device clear
    assert dmm.check().startswith("199")
    assert dmm.read_raw() == pytest.approx(110.0)
    assert dmm.read_celsius() == pytest.approx(pt100_to_celsius(110.0))


def test_keithley199_overflow_is_an_error_not_a_reading():
    """Without the prefix an overflow is all 9s - a large, plausible-looking number."""
    dmm = Keithley199(FakeKeithley199Transport(reading="OOHM+9.999999E+9"))
    with pytest.raises(TransportError, match="overflow"):
        dmm.read_raw()


def test_keithley199_refuses_a_zeroed_reading():
    """Z (fig. 3-6) is a reading with a baseline subtracted: not the Pt100's resistance."""
    dmm = Keithley199(FakeKeithley199Transport(reading="ZOHM+1.000000E+1"))
    with pytest.raises(TransportError):
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


# --- all models, through the app's own factories --------------------------------
@pytest.mark.parametrize("model", ["5302", "sr830"])
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


# --- CND3 ----------------------------------------------------------------------
def test_decode_temperature():
    assert decode_temperature(0x0190) == pytest.approx(40.0)
    assert decode_temperature(0x05DC) == pytest.approx(150.0)
    assert decode_temperature(0xFC18) == pytest.approx(-100.0)
    with pytest.raises(PIDSensorError, match="not connected"):
        decode_temperature(0x8003)


@pytest.mark.parametrize("raw", [0x8000, 0x8001, 0x8005, 0x8008, 0xD8F0])
def test_a_status_code_is_never_read_as_a_temperature(raw):
    """8000H would decode to -3276.8 C: the '-3000 C' spikes at the start of a run."""
    with pytest.raises(PIDSensorError, match="status code"):
        decode_temperature(raw)
    assert decode_temperature(0xD8F1) == pytest.approx(-999.9)  # the lowest real reading


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
    assert "# connection_lockin: Lock-in: SR830 SIMULATED" in text


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


def test_lockin_expand_already_on_at_the_start_is_announced():
    from ferro.instruments.sr830 import SR830Reading

    logs = []
    acq = Acquisition(config.AppConfig(simulate=True), on_log=lambda lvl, m: logs.append(m))
    reading = SR830Reading(1e-3, 0.0, 1e-3, 0.0, 17, expand=True, overloaded=False)
    acq._watch_lockin(reading)
    assert any("offset/expand is on" in m for m in logs)
    logs.clear()
    acq._watch_lockin(reading)  # unchanged: said once
    assert logs == []


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
def _ramp(tr, rate_c_per_min=2.0, amp=0.0, period_s=5.0, top=150.0, dt=0.5):
    """Heat 25 C -> top and cool back; return the directions seen and the peak time."""
    rate = rate_c_per_min / 60
    t_up = (top - 25) / rate
    seen, turns, t = [], [], 0.0
    while t < 2 * t_up:
        base = 25 + rate * t if t < t_up else top - rate * (t - t_up)
        d = tr.update(t, base + amp * math.sin(2 * math.pi * t / period_s))
        if not seen or seen[-1] != d:
            seen.append(d)
        if tr.just_turned:
            turns.append((tr.turn_time_s, tr.turn_temp_c, d))
        t += dt
    return seen, turns, t_up


def test_direction_tracker_heating_then_cooling():
    tr = DirectionTracker()
    seen, turns, t_peak = _ramp(tr)
    assert seen == [0, 1, -1] and tr.segment == 1
    turn_t, turn_temp, d = turns[-1]
    assert d == -1 and abs(turn_t - t_peak) < 10, "the recorded turn is the real peak"
    assert turn_temp == pytest.approx(150, abs=2)


@pytest.mark.parametrize("period_s", [5.0, 30.0, 120.0])
def test_direction_tracker_ignores_a_five_degree_wobble(period_s):
    """Relay cycling swings the probe by degrees; the old slope tracker flipped on each swing."""
    tr = DirectionTracker()
    seen, _, _ = _ramp(tr, amp=5.0, period_s=period_s)
    assert seen == [0, 1, -1] and tr.segment == 1


def test_direction_tracker_does_not_invent_a_ramp_from_a_wobbling_hold():
    tr = DirectionTracker()
    for i in range(2400):  # 20 min holding at 80 C with a +-5 C, 30 s swing
        t = i * 0.5
        tr.update(t, 80 + 5 * math.sin(2 * math.pi * t / 30))
    assert tr.direction == 0 and tr.segment == 0
    tr.update(1200.5, float("nan"))  # a missing reading changes nothing
    assert tr.direction == 0


def test_a_recognised_turn_is_written_to_the_file_and_recolours_the_plot(tmp_path):
    cfg = config.AppConfig(simulate=True)
    logs = []
    acq = Acquisition(cfg, on_log=lambda lvl, m: logs.append(m))

    class Turned:
        segment, slope_c_per_min, just_turned = 1, -2.0, True
        direction, turn_time_s, turn_temp_c = -1, 12.5, 150.1

        def update(self, t, temp):
            return self.direction

    acq.tracker = Turned()
    row = acq._sample()
    assert row["turned_at_s"] == 12.5 and row["direction"] == -1
    assert any("Ramp turned to cooling at 150.1 °C, t = 12 s" in m for m in logs)
    for slot in acq.slots.values():
        slot.close()


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
    skipped = acquisition.DISCARD_TEMPERATURES_AFTER_OPEN
    assert all(r["flags"] == FLAG_TEMP_DISCARDED for r in rows[:skipped]), logs
    assert all(r["flags"] == 0 for r in rows[skipped:]), logs
    files = list(tmp_path.glob("sim_*.txt"))
    assert len(files) == 1
    data = np.loadtxt(files[0])
    assert data.shape[0] >= 4
    text = files[0].read_text()
    assert "lockin_sensitivity: 50 mV" in text and "controller_firmware: V1.00" in text


def test_first_temperatures_after_connecting_are_discarded_not_plotted():
    """Every (re)connect throws away its first readings, flagged rather than nan-and-silent."""
    cfg = config.AppConfig(simulate=True)
    cfg.dmm.enabled = True
    acq = Acquisition(cfg)
    n = acquisition.DISCARD_TEMPERATURES_AFTER_OPEN
    rows = [acq._sample() for _ in range(n + 2)]
    for r in rows[:n]:
        assert math.isnan(r["PV_C"]) and math.isnan(r["T_dmm_C"]) and math.isnan(r["T_C"])
        assert r["flags"] == FLAG_TEMP_DISCARDED
        assert not math.isnan(r["X_V"]), "only temperatures are discarded"
    for r in rows[n:]:
        assert math.isfinite(r["PV_C"]) and math.isfinite(r["T_dmm_C"]) and r["flags"] == 0
    assert acq.slots["pid"].state == "ok", "a discarded reading is not a failure"

    acq.slots["pid"].close()  # a reconnect starts the count again
    acq.slots["pid"].device = None
    assert acq._sample()["flags"] & FLAG_TEMP_DISCARDED
    for slot in acq.slots.values():
        slot.close()


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
    cfg.lockin.model = "5302"
    li = acquisition.open_lockin(cfg)
    assert all(t.kw["retries"] == 0 for t in built)
    assert all(t.kw["reply_delay_s"] == acquisition.LOCKIN_GAP_S for t in built), \
        "the lock-in must be given time to parse a query before it is asked to answer"
    assert li.t.retries == acquisition.LOCKIN_RETRIES, "retries switch on once connected"
    assert li.t.timeout_s == cfg.lockin.timeout_s
