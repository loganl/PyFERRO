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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..transports import Transport, TransportError
from .lockin5302 import checked_index

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

    def settings(self) -> dict:
        sen = self.sensitivity_index()
        tc = self.time_constant_index()
        info = {
            "sensitivity_index": sen,
            "sensitivity": SENSITIVITY_LABELS[sen],
            "time_constant_index": tc,
            "time_constant": TIME_CONSTANT_LABELS[tc],
            "time_constant_s": TIME_CONSTANTS_S[tc],
            "frequency_hz": self.frequency_hz(),
        }
        for ch, name in ((1, "x"), (2, "y"), (3, "r")):
            info[f"{name}_offset_percent"], info[f"{name}_expand"] = self.offset_expand(ch)
        info["expand"] = any(info[f"{n}_offset_percent"] != 0 or info[f"{n}_expand"] != 1
                             for n in ("x", "y", "r"))
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
