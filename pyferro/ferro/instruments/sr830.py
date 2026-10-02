"""Stanford Research Systems SR830 DSP lock-in amplifier, over GPIB.

Command reference: SR830 DSP Lock-In Amplifier manual (docs/manuals), chapter 5,
"Remote Programming" -- the abridged command list on pages 1-7 to 1-9 and the
detailed entries from 5-1 on.

* IEEE 488.2: ``*IDN?``, ``*CLS`` and so on work as usual, unlike the 5302.
* Responses go to only one interface. ``OUTX 1`` sends them to GPIB and has to come
  before any query (5-1, 5-10), so ``__init__`` sends it. The driver is GPIB-only.
* Terminators (5-1, 5-2): commands end with LF or EOI, responses with LF. Open the
  transport with ``write_termination="\\n", read_termination="\\n"``.
* "There is no need to wait between commands" (5-1): the SR830 buffers input and
  holds off the GPIB handshake itself, so the 5302's gap and reply delay are not used.
* ``SNAP? 1,2,3,4`` reads X, Y, R and theta in one query (5-15). X and Y are
  recorded at one instant and R and theta at another, about 10 µs later.
* Values come back in volts and degrees, so nothing is scaled here. The sensitivity is
  still read on every sample: overload and "% of full scale" depend on it.
* ``SENS`` (0..26) and ``OFLT`` (0..19) are table indices (5-6).
* The LIA status byte (5-23) latches: a bit stays set until the byte is read, and
  reading it clears every bit. It is read exactly once per sample, after the data,
  so an overload at any time since the previous sample is caught. Because the read
  clears it, a retried ``LIAS?`` (the transport retries a failed query) may have lost
  the bits, so a sample whose status needed a retry is flagged as overloaded.
* Output offset and expand (``OEXP``, 5-8) are output functions. The manual does not
  say whether ``SNAP?`` values include them, so they are read with every sample and
  reported as ``expand``: when it is on, check the recorded X/Y against the display
  before trusting them.
* The set-up (``setup()``, 5-4 to 5-8): ``FMOD`` reference, ``FREQ``, ``SLVL`` sine
  output level, ``ISRC`` input, ``ICPL`` coupling, ``IGND`` grounding, ``ILIN`` line
  notches, ``RMOD`` reserve, ``OFSL`` filter slope, ``DDEF`` displays, ``PHAS`` phase.
  Unlike the 5302, every one of them can also be set: ``apply()`` sends the ones in
  ``SETTABLE`` (the Lock-in tab), and ``check_setup()`` compares the settings with
  ``CAPACITANCE_SETUP``, the lab manual's SR830 table.
* The sine output has a 50 ohm output impedance (manual 2-6, "Sine Out"): that is the
  R0 of the lab manual's capacitance formula with this lock-in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..transports import Transport, TransportError
from .lockin5302 import _code, _khz, _on_off, _volts, checked_index, commands_for

SENSITIVITIES_V = [
    2e-9, 5e-9, 10e-9, 20e-9, 50e-9,
    100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3, 10e-3, 20e-3, 50e-3,
    100e-3, 200e-3, 500e-3,
    1.0,
]
SENSITIVITY_LABELS = [
    "2 nV", "5 nV", "10 nV", "20 nV", "50 nV", "100 nV", "200 nV", "500 nV",
    "1 µV", "2 µV", "5 µV", "10 µV", "20 µV", "50 µV", "100 µV", "200 µV", "500 µV",
    "1 mV", "2 mV", "5 mV", "10 mV", "20 mV", "50 mV", "100 mV", "200 mV", "500 mV",
    "1 V",
]
TIME_CONSTANTS_S = [
    10e-6, 30e-6, 100e-6, 300e-6, 1e-3, 3e-3, 10e-3, 30e-3, 100e-3, 300e-3,
    1, 3, 10, 30, 100, 300, 1e3, 3e3, 10e3, 30e3,
]
TIME_CONSTANT_LABELS = [
    "10 µs", "30 µs", "100 µs", "300 µs", "1 ms", "3 ms", "10 ms", "30 ms", "100 ms",
    "300 ms", "1 s", "3 s", "10 s", "30 s", "100 s", "300 s", "1 ks", "3 ks", "10 ks",
    "30 ks",
]
EXPAND_FACTORS = [1, 10, 100]  # OEXP expand code 0, 1, 2

# LIA status byte bits (5-23). Bit 3, reference unlock, is expected with no sample.
LIA_INPUT_OVERLOAD = 1 << 0  # input or reserve
LIA_FILTER_OVERLOAD = 1 << 1
LIA_OUTPUT_OVERLOAD = 1 << 2
LIA_OVERLOAD = LIA_INPUT_OVERLOAD | LIA_FILTER_OVERLOAD | LIA_OUTPUT_OVERLOAD

# Set-up codes (manual 5-4 to 5-8). Reference uses the 5302's words, so the two
# models' files and log messages read alike.
REFERENCE_MODES = {0: "EXT", 1: "INT"}  # FMOD
SIGNAL_INPUTS = {0: "A", 1: "A-B", 2: "I (1 MΩ)", 3: "I (100 MΩ)"}  # ISRC
COUPLINGS = {0: "AC", 1: "DC"}  # ICPL
GROUNDINGS = {0: "FLOAT", 1: "GROUND"}  # IGND
LINE_FILTERS = {0: "OUT", 1: "LINE", 2: "2×LINE", 3: "BOTH"}  # ILIN
RESERVE_MODES = {0: "HIGH RESERVE", 1: "NORMAL", 2: "LOW NOISE"}  # RMOD
FILTER_SLOPES = {0: "6 dB/oct", 1: "12 dB/oct", 2: "18 dB/oct", 3: "24 dB/oct"}  # OFSL
CH1_DISPLAYS = {0: "X", 1: "R", 2: "X noise", 3: "AUX IN 1", 4: "AUX IN 2"}  # DDEF 1
CH2_DISPLAYS = {0: "Y", 1: "θ", 2: "Y noise", 3: "AUX IN 3", 4: "AUX IN 4"}  # DDEF 2
DISPLAYS = {"X, Y": "DDEF 1,0,0;DDEF 2,0,0", "R, θ": "DDEF 1,1,0;DDEF 2,1,0"}  # what can be set

# The lab manual's SR830 table for the capacitance measurement (ptmanual/main.tex,
# tab:lockinsr830): keep the two in step. Most of it is the SR830's standard settings
# (manual 4-4), which [Setup] held down at power-on, or *RST, restores.
# (settings key, name, wanted - a tuple means any of them, formatter, how to change it)
CAPACITANCE_SETUP = (
    ("reference_mode", "Reference", "INT", str, "Source in the REFERENCE section"),
    ("oscillator_hz", "Oscillator frequency", 25000.0, _khz, "Freq, then the knob"),
    ("oscillator_v", "Oscillator level", 0.5, _volts, "Ampl, then the knob"),
    ("signal_input", "Signal input", "A", str, "Input in SIGNAL INPUT"),
    ("coupling", "Coupling", "AC", str, "Couple in SIGNAL INPUT"),
    ("grounding", "Grounding", "FLOAT", str, "Ground in SIGNAL INPUT"),
    ("line_filter", "Line notch filters", "OUT", str, "Notch in FILTERS"),
    ("sensitivity", "Sensitivity", "1 V", str, "the SENSITIVITY arrow keys"),
    ("dynamic_reserve", "Dynamic reserve", "LOW NOISE", str, "Reserve in RESERVE"),
    ("time_constant", "Time constant", "300 ms", str, "the TIME CONSTANT arrow keys"),
    ("filter_slope", "Filter slope", "12 dB/oct", str, "Slope/Oct in TIME CONSTANT"),
    ("expand", "Offset and expand", False, _on_off, "Offset On/Off and Expand, each channel"),
    ("display", "Displays", "X, Y", str, "Display in each channel"),
)


def frequency_command(hz: float) -> str:
    if not 0.001 <= hz <= 102000:
        raise ValueError(f"reference frequency {hz:g} Hz is outside 1 mHz to 102 kHz")
    return f"FREQ {hz:.4f}"


def amplitude_command(volts: float) -> str:
    if not 0.004 <= volts <= 5.0:
        raise ValueError(f"sine output level {volts:g} V is outside 4 mV to 5 V")
    return f"SLVL {volts:.3f}"


def expand_command(on: bool) -> str:
    if on:
        raise ValueError("set an offset or expand on the front panel; PyFERRO only turns them off")
    return "OEXP 1,0,0;OEXP 2,0,0;OEXP 3,0,0"  # an offset of zero is off (5-8)


def display_command(value: str) -> str:
    if value not in DISPLAYS:
        raise ValueError(f"{value!r} is not one of {', '.join(DISPLAYS)}")
    return DISPLAYS[value]


# What apply() can set, in the order it sends them. The reference goes first: FREQ is
# refused unless it is internal (5-4). The reserve and slope go before the time
# constant, which is raised to the shortest one they allow (5-6).
# (settings key, command builder, description of the new value)
SETTABLE = (
    ("reference_mode", lambda v: f"FMOD {_code(REFERENCE_MODES, v)}", lambda v: f"reference {v}"),
    ("oscillator_hz", frequency_command, lambda v: f"oscillator {_khz(v)}"),
    ("oscillator_v", amplitude_command, lambda v: f"oscillator {_volts(v)}"),
    ("signal_input", lambda v: f"ISRC {_code(SIGNAL_INPUTS, v)}", lambda v: f"input {v}"),
    ("coupling", lambda v: f"ICPL {_code(COUPLINGS, v)}", lambda v: f"coupling {v}"),
    ("grounding", lambda v: f"IGND {_code(GROUNDINGS, v)}", lambda v: f"grounding {v}"),
    ("line_filter", lambda v: f"ILIN {_code(LINE_FILTERS, v)}", lambda v: f"line notches {v}"),
    ("sensitivity_index", lambda v: f"SENS {int(v)}",
     lambda v: f"sensitivity {SENSITIVITY_LABELS[int(v)]}"),
    ("dynamic_reserve", lambda v: f"RMOD {_code(RESERVE_MODES, v)}", lambda v: f"reserve {v}"),
    ("filter_slope", lambda v: f"OFSL {_code(FILTER_SLOPES, v)}", lambda v: f"slope {v}"),
    ("time_constant_index", lambda v: f"OFLT {int(v)}",
     lambda v: f"time constant {TIME_CONSTANT_LABELS[int(v)]}"),
    ("expand", expand_command, lambda v: "offsets and expands off"),
    ("display", display_command, lambda v: f"displays {v}"),
)

# The Lock-in tab's controls (kinds as in lockin5302.PANEL).
PANEL = (
    ("reference_mode", "Reference", "choice", tuple(REFERENCE_MODES.values())),
    ("oscillator_hz", "Oscillator frequency", "khz", (0.001, 102.0)),
    ("oscillator_v", "Oscillator level", "volts", (0.004, 5.0)),
    ("signal_input", "Signal input", "choice", tuple(SIGNAL_INPUTS.values())),
    ("coupling", "Coupling", "choice", tuple(COUPLINGS.values())),
    ("grounding", "Grounding", "choice", tuple(GROUNDINGS.values())),
    ("line_filter", "Line notch filters", "choice", tuple(LINE_FILTERS.values())),
    ("sensitivity_index", "Sensitivity", "index", tuple(SENSITIVITY_LABELS)),
    ("dynamic_reserve", "Dynamic reserve", "choice", tuple(RESERVE_MODES.values())),
    ("time_constant_index", "Time constant", "index", tuple(TIME_CONSTANT_LABELS)),
    ("filter_slope", "Filter slope", "choice", tuple(FILTER_SLOPES.values())),
    ("expand", "Offset and expand", "check", "On (turn off here, set on the front panel)"),
    ("display", "Displays", "choice", tuple(DISPLAYS)),
)

_FLOAT = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def parse_floats(text: str, expected: int) -> list[float]:
    """Pull ``expected`` numbers out of a comma-separated response."""
    values = [float(v) for v in _FLOAT.findall(text)]
    if len(values) != expected:
        raise TransportError(f"expected {expected} number(s), got {text!r}")
    return values


@dataclass
class SR830Reading:
    x_v: float
    y_v: float
    r_v: float
    theta_deg: float
    sen_index: int
    expand: bool
    overloaded: bool

    EXPAND_NAME = "output offset/expand"

    @property
    def sensitivity(self) -> str:
        return SENSITIVITY_LABELS[self.sen_index]

    @property
    def full_scale_v(self) -> float:
        return SENSITIVITIES_V[self.sen_index]

    @property
    def percent_fs(self) -> float:
        return 100.0 * max(abs(self.x_v), abs(self.y_v)) / self.full_scale_v


class SR830:
    MODEL = "SR830"
    SENSITIVITY_LABELS = SENSITIVITY_LABELS
    TIME_CONSTANT_LABELS = TIME_CONSTANT_LABELS
    EXPAND_NAME = SR830Reading.EXPAND_NAME
    # What the Test button, the file header and the Lock-in tab need to know.
    SETUP_TABLE = CAPACITANCE_SETUP
    SETUP_NAME = "the lab manual's SR830 table"
    SETUP_NOTE = ""
    PANEL = PANEL
    PANEL_NOTE = ("Front panel only: the phase tuning ([Phase] in the AUTO section, with the "
                  "sample disconnected) and setting an offset or expand.")

    @staticmethod
    def commands(changes: dict) -> list[tuple[str, str]]:
        return commands_for(changes, SETTABLE)

    def __init__(self, transport: Transport) -> None:
        self.t = transport
        self.t.write("OUTX1")
        # Clear the latched status bits, so the first sample does not report an
        # overload that happened before this program connected.
        self.t.write("*CLS")

    # --- identification / settings -------------------------------------
    def identify(self) -> str:
        return self.t.query("*IDN?")

    def check(self) -> str:
        ident = self.identify()
        if "SR830" not in ident:
            raise TransportError(f"expected an SR830, instrument answered {ident!r}")
        return ident

    def sensitivity_index(self) -> int:
        return checked_index(int(parse_floats(self.t.query("SENS?"), 1)[0]),
                             len(SENSITIVITIES_V), "SENS")

    def time_constant_index(self) -> int:
        return checked_index(int(parse_floats(self.t.query("OFLT?"), 1)[0]),
                             len(TIME_CONSTANTS_S), "OFLT")

    def frequency_hz(self) -> float:
        return parse_floats(self.t.query("FREQ?"), 1)[0]

    def offset_expand(self, channel: int) -> tuple[float, int]:
        """(offset in % of full scale, expand factor) for X (1), Y (2) or R (3)."""
        offset, code = parse_floats(self.t.query(f"OEXP?{channel}"), 2)
        return offset, EXPAND_FACTORS[checked_index(int(code), len(EXPAND_FACTORS), "OEXP")]

    def expand(self) -> bool:
        """True when any recorded output (X, Y or R) has an offset or an expand."""
        return any(self.offset_expand(ch) != (0.0, 1) for ch in (1, 2, 3))

    def set_sensitivity(self, index: int) -> None:
        self.t.write(f"SENS {int(index)}")

    def set_time_constant(self, index: int) -> None:
        self.t.write(f"OFLT {int(index)}")

    def displays(self) -> str:
        """What CH1 and CH2 show, e.g. "X, Y"; a ratio is noted (DDEF?, 5-8)."""
        parts = []
        for ch, names in ((1, CH1_DISPLAYS), (2, CH2_DISPLAYS)):
            j, k = (int(v) for v in parse_floats(self.t.query(f"DDEF?{ch}"), 2))
            parts.append(names.get(j, f"? ({j})") + (" ratio" if k else ""))
        return ", ".join(parts)

    def setup(self) -> dict:
        """The rest of the set-up, each value read on its own.

        A value that cannot be read is None rather than a failed Test or header:
        these describe the run, they are not measurements.
        """
        def code(command, names):
            value = int(parse_floats(self.t.query(command), 1)[0])
            return names.get(value, f"? ({value})")

        def number(command):
            return parse_floats(self.t.query(command), 1)[0]

        reads = {
            "reference_mode": lambda: code("FMOD?", REFERENCE_MODES),
            "oscillator_v": lambda: number("SLVL?"),
            "signal_input": lambda: code("ISRC?", SIGNAL_INPUTS),
            "coupling": lambda: code("ICPL?", COUPLINGS),
            "grounding": lambda: code("IGND?", GROUNDINGS),
            "line_filter": lambda: code("ILIN?", LINE_FILTERS),
            "dynamic_reserve": lambda: code("RMOD?", RESERVE_MODES),
            "filter_slope": lambda: code("OFSL?", FILTER_SLOPES),
            "display": self.displays,
            "phase_deg": lambda: number("PHAS?"),
        }
        out = {}
        for key, read in reads.items():
            try:
                out[key] = read()
            except TransportError:
                out[key] = None
        return out

    def apply(self, changes: dict) -> list[str]:
        """Send new settings (keys as in SETTABLE); returns what was sent, described.

        Every value is checked before the first command goes, so a bad one sends
        nothing. Read the settings back afterwards to confirm them.
        """
        commands = self.commands(changes)
        for command, _ in commands:
            self.t.write(command)
        return [f"{what} ({command})" for command, what in commands]

    def settings(self) -> dict:
        sen = self.sensitivity_index()
        tc = self.time_constant_index()
        freq = self.frequency_hz()
        info = {
            "sensitivity_index": sen,
            "sensitivity": SENSITIVITY_LABELS[sen],
            "time_constant_index": tc,
            "time_constant": TIME_CONSTANT_LABELS[tc],
            "time_constant_s": TIME_CONSTANTS_S[tc],
            "frequency_hz": freq,
            # The sine output runs at the reference frequency, internal or external.
            "oscillator_hz": freq,
        }
        for ch, name in ((1, "x"), (2, "y"), (3, "r")):
            info[f"{name}_offset_percent"], info[f"{name}_expand"] = self.offset_expand(ch)
        info["expand"] = any(info[f"{n}_offset_percent"] != 0 or info[f"{n}_expand"] != 1
                             for n in ("x", "y", "r"))
        info.update(self.setup())
        return info

    # --- data ----------------------------------------------------------
    def read(self) -> SR830Reading:
        sen = self.sensitivity_index()
        exp = self.expand()
        x, y, r, theta = parse_floats(self.t.query("SNAP?1,2,3,4"), 4)
        retries_before = getattr(self.t, "retries_used", 0)
        status = int(parse_floats(self.t.query("LIAS?"), 1)[0])
        # A lost reply may have cleared the byte before the retry read it: assume the worst.
        status_lost = getattr(self.t, "retries_used", 0) != retries_before
        return SR830Reading(x_v=x, y_v=y, r_v=r, theta_deg=theta, sen_index=sen, expand=exp,
                            overloaded=status_lost or bool(status & LIA_OVERLOAD))

    def close(self) -> None:
        self.t.close()
