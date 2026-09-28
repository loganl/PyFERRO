"""Stanford Research Systems SR830 DSP lock-in amplifier.

Command reference: SR830 DSP Lock-In Amplifier manual (docs/manuals), chapter 5,
"Remote Programming" -- the abridged command list on its page 1-7/1-8 and the
detailed entries on 5-3 through 5-24.

* IEEE 488.2 compliant: ``*IDN?``, ``*RST``, ``*CLS`` etc. work as usual, unlike the
  EG&G 5302 (see lockin5302.py) which predates SCPI entirely.
* ``OUTX 1`` must be sent once after opening the GPIB connection, or the instrument
  keeps answering queries over RS232 instead (manual 5-10). This driver does it in
  ``__init__``.
* The GPIB terminator is LF (write) and LF-or-EOI (read) -- manual page 5-4 -- not
  the 5302's CR. Open the transport with ``write_termination="\\n",
  read_termination="\\n"``.
* ``SNAP? 1,2,3,4,9`` reads X, Y, R, theta and the reference frequency as one atomic
  query (manual 5-15), so a short time constant can't skew X relative to Y the way
  two separate ``OUTP?`` queries would.
* ``SENS`` (0..26) and ``OFLT`` (0..19) are table indices, not units -- manual 5-6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..transports import Transport, TransportError

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
FILTER_SLOPES_DB_OCT = [6, 12, 18, 24]

# LIA status byte, manual page 5-23. Bits 0-2 are the overload flags; bit 3 is
# reference unlock, which (as with the 5302) is expected with no sample connected.
LIA_RESERVE_OR_INPUT_OVERLOAD = 1 << 0
LIA_FILTER_OVERLOAD = 1 << 1
LIA_OUTPUT_OVERLOAD = 1 << 2
LIA_UNLOCKED = 1 << 3

_FLOAT = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_floats(text: str, expected: int) -> list[float]:
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
    freq_hz: float


class SR830:
    def __init__(self, transport: Transport) -> None:
        self.t = transport
        # Route responses to GPIB, not RS232 -- see module docstring.
        self.t.write("OUTX1")

    # --- identification / settings -------------------------------------
    def identify(self) -> str:
        return self.t.query("*IDN?")

    def check(self) -> str:
        ident = self.identify()
        if "SR830" not in ident:
            raise TransportError(f"expected an SR830 *IDN? reply, instrument answered {ident!r}")
        return ident

    def sensitivity_index(self) -> int:
        return int(self.t.query("SENS?"))

    def time_constant_index(self) -> int:
        return int(self.t.query("OFLT?"))

    def filter_slope_index(self) -> int:
        return int(self.t.query("OFSL?"))

    def frequency_hz(self) -> float:
        return float(self.t.query("FREQ?"))

    def phase_deg(self) -> float:
        return float(self.t.query("PHAS?"))

    def reference_internal(self) -> bool:
        return self.t.query("FMOD?").strip() == "1"

    def set_sensitivity(self, index: int) -> None:
        self.t.write(f"SENS{int(index)}")

    def set_time_constant(self, index: int) -> None:
        self.t.write(f"OFLT{int(index)}")

    def settings(self) -> dict:
        sen = self.sensitivity_index()
        tc = self.time_constant_index()
        return {
            "sensitivity_index": sen,
            "sensitivity": SENSITIVITY_LABELS[sen],
            "time_constant_index": tc,
            "time_constant": TIME_CONSTANT_LABELS[tc],
            "time_constant_s": TIME_CONSTANTS_S[tc],
            "frequency_hz": self.frequency_hz(),
            "reference_internal": self.reference_internal(),
        }

    # --- data ------------------------------------------------------------
    def read(self) -> SR830Reading:
        # One atomic snapshot of X, Y, R, theta and the reference frequency
        # (manual 5-15) instead of five separate queries.
        x, y, r, theta, freq = parse_floats(self.t.query("SNAP?1,2,3,4,9"), 5)
        return SR830Reading(x_v=x, y_v=y, r_v=r, theta_deg=theta, freq_hz=freq)

    def status_byte(self) -> int:
        return int(self.t.query("LIAS?"))

    def overloaded(self) -> bool:
        stb = self.status_byte()
        return bool(stb & (LIA_RESERVE_OR_INPUT_OVERLOAD | LIA_FILTER_OVERLOAD | LIA_OUTPUT_OVERLOAD))

    def unlocked(self) -> bool:
        return bool(self.status_byte() & LIA_UNLOCKED)

    def close(self) -> None:
        self.t.close()
