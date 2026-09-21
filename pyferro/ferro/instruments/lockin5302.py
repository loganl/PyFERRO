"""EG&G / PAR Model 5302 lock-in amplifier.

Command reference: 5302 Instruction Manual (221490-A-MNL-F), chapter 9.

* ``XY``  -> two integers, full scale = +/-10000 (range +/-12000)
* ``SEN`` -> sensitivity index 0..21 (100 nV .. 1 V, 1-2-5 sequence)
* ``EX``  -> 1 when Expand X is on: the x channel's gain x10, y unaffected
* ``XTC`` -> output time-constant index 0..18
* ``FRQ`` -> reference frequency in mHz
* ``ID``  -> "5302"

Volts are computed as ``counts / 10000 * full_scale`` using the sensitivity read
back from the instrument, so changing SEN on the front panel mid-run (or an
auto-sensitivity step) is picked up automatically.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ..transports import Transport, TransportError

SENSITIVITIES_V = [
    100e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6,
    10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3,
    10e-3, 20e-3, 50e-3,
    100e-3, 200e-3, 500e-3,
    1.0,
]
SENSITIVITY_LABELS = [
    "100 nV", "200 nV", "500 nV", "1 µV", "2 µV", "5 µV", "10 µV", "20 µV", "50 µV",
    "100 µV", "200 µV", "500 µV", "1 mV", "2 mV", "5 mV", "10 mV", "20 mV", "50 mV",
    "100 mV", "200 mV", "500 mV", "1 V",
]
TIME_CONSTANTS_S = [
    float("nan"), 100e-6, 1e-3, 10e-3, 20e-3, 50e-3, 100e-3, 200e-3, 500e-3,
    1, 2, 5, 10, 20, 50, 100, 200, 500, 1000,
]
TIME_CONSTANT_LABELS = [
    "F MIN", "F 100 µs", "F 1 ms", "F 10 ms", "20 ms", "50 ms", "100 ms", "200 ms",
    "500 ms", "1 s", "2 s", "5 s", "10 s", "20 s", "50 s", "100 s", "200 s", "500 s",
    "1000 s",
]
FULL_SCALE_COUNTS = 10000
OVERLOAD_COUNTS = 12000

_INT = re.compile(r"[-+]?\d+")


def parse_ints(text: str, expected: int) -> list[int]:
    """Pull ``expected`` integers out of a response, whatever the delimiter."""
    values = [int(v) for v in _INT.findall(text)]
    if len(values) != expected:
        raise TransportError(f"expected {expected} number(s), got {text!r}")
    return values


def checked_index(value: int, size: int, command: str) -> int:
    """Refuse a setting index the instrument cannot really have.

    Out of range means the reply is out of step with the command, not that the
    instrument has a range we do not know about: SEN is 0-21 and XTC 0-18 in the
    manual (tables 9-16 and 9-19), and both match the tables above.
    """
    if not 0 <= value < size:
        raise TransportError(
            f"{command} answered {value}, outside 0..{size - 1}. The replies are out "
            "of step with the commands - the instrument is answering the previous one.")
    return value


@dataclass
class LockinReading:
    x_counts: int
    y_counts: int
    sen_index: int
    expand: bool
    x_v: float
    y_v: float

    @property
    def r_v(self) -> float:
        return math.hypot(self.x_v, self.y_v)

    @property
    def theta_deg(self) -> float:
        return math.degrees(math.atan2(self.y_v, self.x_v))

    @property
    def full_scale_v(self) -> float:
        return SENSITIVITIES_V[self.sen_index]

    @property
    def overloaded(self) -> bool:
        return max(abs(self.x_counts), abs(self.y_counts)) >= OVERLOAD_COUNTS

    @property
    def percent_fs(self) -> float:
        return 100.0 * max(abs(self.x_counts), abs(self.y_counts)) / FULL_SCALE_COUNTS


def counts_to_volts(counts: int, sen_index: int, expand: bool = False) -> float:
    """Scale a reading. ``expand`` applies to the X channel only.

    Expand multiplies the gain of the x demodulator channel by 10 (manual sections
    4 and 9, command EX); Y keeps its full scale. Pass ``expand`` for X and leave it
    False for Y.
    """
    if not 0 <= sen_index < len(SENSITIVITIES_V):
        raise TransportError(f"sensitivity index {sen_index} out of range")
    volts = counts / FULL_SCALE_COUNTS * SENSITIVITIES_V[sen_index]
    return volts / 10.0 if expand else volts


class Lockin5302:
    def __init__(self, transport: Transport) -> None:
        self.t = transport

    # --- identification / settings -------------------------------------
    def identify(self) -> str:
        return self.t.query("ID")

    def check(self) -> str:
        ident = self.identify()
        if "5302" not in ident:
            raise TransportError(f"expected ID 5302, instrument answered {ident!r}")
        return ident

    def sensitivity_index(self) -> int:
        return checked_index(parse_ints(self.t.query("SEN"), 1)[0], len(SENSITIVITIES_V), "SEN")

    def expand(self) -> bool:
        # A failure raises like any other, so that sample is recorded as missing.
        # Assuming "off" instead would scale X ten times too large whenever expand
        # is on and one exchange happened to drop - silently, in the data.
        return parse_ints(self.t.query("EX"), 1)[0] == 1

    def time_constant_index(self) -> int:
        return checked_index(parse_ints(self.t.query("XTC"), 1)[0], len(TIME_CONSTANTS_S), "XTC")

    def frequency_hz(self) -> float:
        return parse_ints(self.t.query("FRQ"), 1)[0] / 1000.0

    def set_sensitivity(self, index: int) -> None:
        self.t.write(f"SEN {int(index)}")

    def set_time_constant(self, index: int) -> None:
        self.t.write(f"XTC {int(index)}")

    def settings(self) -> dict:
        sen = self.sensitivity_index()
        tc = self.time_constant_index()
        return {
            "sensitivity_index": sen,
            "sensitivity": SENSITIVITY_LABELS[sen],
            "time_constant_index": tc,
            "time_constant": TIME_CONSTANT_LABELS[tc],
            "time_constant_s": TIME_CONSTANTS_S[tc],
            "expand": self.expand(),
            "frequency_hz": self.frequency_hz(),
        }

    # --- data ----------------------------------------------------------
    def read(self) -> LockinReading:
        sen = self.sensitivity_index()
        exp = self.expand()
        # XY is the compound command X;Y (manual ch. 9). Depending on the port and
        # terminator settings the two values arrive on one line (with the DD delimiter)
        # or as two terminated lines; over GPIB the second line needs its own read.
        text = self.t.query("XY")
        values = [int(v) for v in _INT.findall(text)]
        if len(values) == 1 and hasattr(self.t, "read"):
            values += [int(v) for v in _INT.findall(self.t.read())]
        x, y = parse_ints(" ".join(map(str, values)), 2) if len(values) != 2 else values
        return LockinReading(
            x_counts=x,
            y_counts=y,
            sen_index=sen,
            expand=exp,
            x_v=counts_to_volts(x, sen, exp),
            y_v=counts_to_volts(y, sen),  # expand is X only
        )

    def close(self) -> None:
        self.t.close()
