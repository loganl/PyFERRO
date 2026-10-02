"""Simulated instruments for practice runs and tests.

They sit *below* the real drivers (a fake 5302 command transport and a fake
Modbus register map) so simulation exercises the same parsing and scaling code
that talks to the hardware.
"""

from __future__ import annotations

import math
import random
import threading
import time

from ..transports import Transport, TransportError
from .hp34401a import celsius_to_pt100
from .lockin5302 import SENSITIVITIES_V
from .sr830 import SENSITIVITIES_V as SR830_SENSITIVITIES_V


class SimulatedSample:
    """A BaTiO3-like sample heated to 150 degC and cooled, with a peak at Tc."""

    def __init__(self, rate_c_per_min: float = 30.0, t_start: float | None = None) -> None:
        self.t0 = time.monotonic() if t_start is None else t_start
        self.rate = rate_c_per_min / 60.0
        self.low, self.high, self.tc = 25.0, 150.0, 122.0
        self._lock = threading.Lock()

    def temperature(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        span = self.high - self.low
        phase = ((now - self.t0) * self.rate) % (2 * span)
        target = self.low + (phase if phase < span else 2 * span - phase)
        return target + random.gauss(0, 0.02)

    def setpoint(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        span = self.high - self.low
        phase = ((now - self.t0) * self.rate) % (2 * span)
        return self.high if phase < span else self.low

    def signal_v(self, temp_c: float) -> tuple[float, float]:
        width = 3.0
        peak = 1.0 / (1.0 + ((temp_c - self.tc) / width) ** 2)
        y = -(2.0e-3 + 6.0e-3 * peak) * (1 + random.gauss(0, 0.002))
        x = 1.5e-4 + 4e-4 * peak + random.gauss(0, 5e-6)
        return x, y


class SimLockinTransport(Transport):
    name = "SIM:LOCKIN"

    def __init__(self, sample: SimulatedSample) -> None:
        self.sample = sample
        self.sen = 17  # 50 mV
        self.xtc = 7  # 200 ms
        self.expand = 0
        # The capacitance set-up of the lab manual's 5302 table: INT, 0.500 V at 25 kHz,
        # preamplifier input.
        self.setup = {"IE": "0", "OA": "5000 1", "OF": "2500 7", "DR": "1", "FLT": "0",
                      "PREAMP": "1", "P": "0 0"}

    def write(self, cmd: str) -> None:
        self.query(cmd)

    def query(self, cmd: str) -> str:
        parts = cmd.strip().upper().split()
        if not parts:
            raise TransportError("empty command")
        name, args = parts[0], parts[1:]
        if name == "ID":
            return "5302"
        if name == "SEN":
            if args:
                self.sen = int(args[0])
                return ""
            return str(self.sen)
        if name == "XTC":
            if args:
                self.xtc = int(args[0])
                return ""
            return str(self.xtc)
        if name == "EX":
            if args:
                self.expand = int(args[0])
                return ""
            return str(self.expand)
        if name == "FRQ":
            return "25000000"
        if name in self.setup:  # answers its value bare, sets it with arguments
            if args:
                self.setup[name] = " ".join(args)
                return ""
            return self.setup[name]
        if name == "XY":
            x, y = self.sample.signal_v(self.sample.temperature())
            fs = SENSITIVITIES_V[self.sen]
            clip = lambda v, scale: max(-12000, min(12000, round(v / scale * 10000)))
            # Expand X raises the x channel's gain tenfold; y is unaffected.
            return f"{clip(x, fs / 10 if self.expand else fs)},{clip(y, fs)}"
        raise TransportError(f"SIM lock-in: unknown command {cmd!r}")


class SimSR830Transport(Transport):
    """The SR830's ASCII commands (manual ch. 5), answering from the simulated sample."""

    name = "SIM:SR830"

    def __init__(self, sample: SimulatedSample) -> None:
        self.sample = sample
        self.sens = 22  # 50 mV
        self.oflt = 9  # 300 ms
        self.lias = 0

    def write(self, cmd: str) -> None:
        self.query(cmd)

    def query(self, cmd: str) -> str:
        cmd = cmd.strip().upper().replace(" ", "")
        if cmd in ("OUTX1", "*CLS"):
            self.lias = 0
            return ""
        if cmd == "*IDN?":
            return "Stanford_Research_Systems,SR830,s/n00000,ver1.07"
        if cmd == "SENS?":
            return str(self.sens)
        if cmd.startswith("SENS"):
            self.sens = int(cmd[4:])
            return ""
        if cmd == "OFLT?":
            return str(self.oflt)
        if cmd.startswith("OFLT"):
            self.oflt = int(cmd[4:])
            return ""
        if cmd == "FREQ?":
            return "1000.000"
        if cmd.startswith("OEXP?"):
            return "0.00,0"
        if cmd == "SNAP?1,2,3,4":
            x, y = self.sample.signal_v(self.sample.temperature())
            if max(abs(x), abs(y)) > SR830_SENSITIVITIES_V[self.sens]:
                self.lias |= 1 << 2  # output overload, latched until read
            r, theta = math.hypot(x, y), math.degrees(math.atan2(y, x))
            return f"{x:.6e},{y:.6e},{r:.6e},{theta:.3f}"
        if cmd == "LIAS?":
            value, self.lias = self.lias, 0
            return str(value)
        raise TransportError(f"SIM SR830: unknown command {cmd!r}")


class SimModbusInstrument:
    """Minimal stand-in for ``minimalmodbus.Instrument`` holding a CND3 register map."""

    class _Serial:
        def close(self) -> None:
            pass

    def __init__(self, sample: SimulatedSample) -> None:
        self.sample = sample
        self.serial = self._Serial()

    def read_registers(self, register: int, count: int, functioncode: int = 3) -> list[int]:
        def word(celsius: float) -> int:
            return int(round(celsius * 10)) & 0xFFFF

        now = time.monotonic()
        regs = {
            0x1000: word(self.sample.temperature(now) + 0.3),
            0x1001: word(self.sample.setpoint(now)),
            0x1005: 0,
            0x1012: 455,
            0x1013: 0,
            0x102A: 0b0100,
            0x102F: 0x0100,
            0x103C: 1,
            0x1104: 0,
            0x110E: 1000,
            0x1120: 0,
            0x1124: 0,
        }
        return [regs.get(register + i, 0) for i in range(count)]


class SimDMMTransport(Transport):
    name = "SIM:DMM"

    def __init__(self, sample: SimulatedSample) -> None:
        self.sample = sample

    def write(self, cmd: str) -> None:
        pass

    def query(self, cmd: str) -> str:
        if cmd.upper().startswith("*IDN"):
            return "HEWLETT-PACKARD,34401A,0,SIM"
        return f"{celsius_to_pt100(self.sample.temperature()):.5f}"


class SimK199Transport(Transport):
    """A Keithley 199 in ohms with prefixed readings (manual 3.9)."""

    name = "SIM:K199"

    def __init__(self, sample: SimulatedSample) -> None:
        self.sample = sample

    def write(self, cmd: str) -> None:
        pass

    def query(self, cmd: str) -> str:
        if "U0" in cmd.upper():
            return "199110020000000000410600000000000"
        return f"NOHM{celsius_to_pt100(self.sample.temperature()):+.6E}"


def lockin_transport(model: str, sample: SimulatedSample) -> Transport:
    return {"5302": SimLockinTransport, "sr830": SimSR830Transport}[model](sample)


def dmm_transport(model: str, sample: SimulatedSample) -> Transport:
    return {"34401a": SimDMMTransport, "k199": SimK199Transport}[model](sample)


_shared_sample: SimulatedSample | None = None


def shared_sample() -> SimulatedSample:
    """One simulated sample per process so all simulated instruments agree."""
    global _shared_sample
    if _shared_sample is None:
        _shared_sample = SimulatedSample()
    return _shared_sample
