"""HP/Agilent 34401A multimeter reading a Pt100 sample thermometer (optional).

The LabVIEW routine sent ``read?`` to GPIB0::24::INSTR.  The meter should be in
4-wire ohms (Omega) mode; the resistance is converted to temperature with the
IEC 60751 Callendar-Van Dusen equation (valid 0-850 degC, which covers this lab).
"""

from __future__ import annotations

import math

from ..transports import Transport, TransportError

A = 3.9083e-3
B = -5.775e-7


def pt100_to_celsius(r_ohm: float, r0: float = 100.0) -> float:
    """Invert R = R0 (1 + A T + B T^2)."""
    disc = A * A - 4 * B * (1 - r_ohm / r0)
    if disc < 0:
        raise ValueError(f"resistance {r_ohm} ohm is outside the Pt100 range")
    return (-A + math.sqrt(disc)) / (2 * B)


def celsius_to_pt100(t_c: float, r0: float = 100.0) -> float:
    return r0 * (1 + A * t_c + B * t_c * t_c)


class HP34401A:
    def __init__(self, transport: Transport, mode: str = "pt100", r0: float = 100.0) -> None:
        self.t = transport
        self.mode = mode  # "pt100" (reading is ohms) or "celsius" (reading already degC)
        self.r0 = r0

    def identify(self) -> str:
        return self.t.query("*IDN?")

    def read_raw(self) -> float:
        text = self.t.query("READ?")
        try:
            return float(text)
        except ValueError as exc:
            raise TransportError(f"multimeter answered {text!r}") from exc

    def read_celsius(self) -> float:
        value = self.read_raw()
        if self.mode == "celsius":
            return value
        if not 0.5 * self.r0 < value < 3.0 * self.r0:
            raise TransportError(
                f"multimeter reads {value:g}; expected ~{self.r0:g}-{2 * self.r0:g} ohm. "
                "Is it in 4-wire ohms mode with the Pt100 connected?"
            )
        return pt100_to_celsius(value, self.r0)

    def close(self) -> None:
        self.t.close()
