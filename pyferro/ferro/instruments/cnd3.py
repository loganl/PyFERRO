"""Omega CND3 series PID temperature controller over RS-485 (Modbus).

Reference: Omega CND3 Series User's Guide, "RS-485 Communication" section.

* Modbus ASCII (factory default) or RTU, address 1, 9600 bps, 7 data bits,
  even parity, 1 stop bit (7N1, 8O2 and 8E2 are not supported by the unit).
* Function 03H reads up to 8 words.  The app only ever *reads* - it never
  writes to the controller, so it cannot change setpoints or heater power.
* 1000H PV and 1001H SV are signed, 0.1 degree units.  PV reads 8002H..8007H
  when the sensor has a fault.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

REG_PV = 0x1000
REG_SV = 0x1001
REG_SENSOR_TYPE = 0x1004
REG_CONTROL_METHOD = 0x1005
REG_OUTPUT1 = 0x1012
REG_LED = 0x102A
REG_VERSION = 0x102F
REG_RUN_STOP = 0x103C

PV_ERRORS = {
    0x8002: "controller initialising (no temperature yet)",
    0x8003: "temperature sensor not connected",
    0x8004: "temperature sensor input error",
    0x8006: "controller ADC input error",
    0x8007: "controller memory read/write error",
}
CONTROL_METHODS = {0: "PID", 1: "ON/OFF", 2: "Manual", 3: "Fuzzy"}
RUN_STATES = {0: "STOP", 1: "RUN", 2: "END", 3: "HOLD"}
UNSUPPORTED_FORMATS = {(7, "N", 1), (8, "O", 2), (8, "E", 2)}
BAUD_RATES = [9600, 19200, 38400, 4800, 2400]


class PIDError(RuntimeError):
    """Communication problem with the controller."""


class PIDSensorError(PIDError):
    """Controller answered, but reports a sensor fault instead of a temperature."""


def decode_temperature(raw: int) -> float:
    if raw in PV_ERRORS:
        raise PIDSensorError(PV_ERRORS[raw])
    if raw >= 0x8000:
        raw -= 0x10000
    return raw / 10.0


@dataclass
class PIDReading:
    pv_c: float
    sv_c: float


class CND3:
    def __init__(
        self,
        port: str,
        address: int = 1,
        mode: str = "ascii",
        baudrate: int = 9600,
        bytesize: int = 7,
        parity: str = "E",
        stopbits: int = 1,
        timeout_s: float = 0.5,
        retries: int = 2,
        instrument=None,
    ) -> None:
        parity = parity.upper()[:1]
        if (bytesize, parity, stopbits) in UNSUPPORTED_FORMATS:
            raise PIDError(f"The CND3 does not support {bytesize}{parity}{stopbits} framing")
        self.port = port
        self.address = address
        self.retries = retries
        if instrument is None:
            import minimalmodbus
            import serial

            try:
                instrument = minimalmodbus.Instrument(
                    port,
                    address,
                    mode=minimalmodbus.MODE_ASCII if mode.lower() == "ascii" else minimalmodbus.MODE_RTU,
                    close_port_after_each_call=False,
                )
            except Exception as exc:
                raise PIDError(f"Could not open {port}: {exc}") from exc
            instrument.serial.baudrate = baudrate
            instrument.serial.bytesize = bytesize
            instrument.serial.parity = {"E": serial.PARITY_EVEN, "O": serial.PARITY_ODD, "N": serial.PARITY_NONE}[parity]
            instrument.serial.stopbits = stopbits
            instrument.serial.timeout = timeout_s
            instrument.clear_buffers_before_each_transaction = True
        self.inst = instrument

    # ------------------------------------------------------------------
    def _read(self, register: int, count: int = 1) -> list[int]:
        last = None
        for attempt in range(self.retries + 1):
            try:
                return list(self.inst.read_registers(register, count, functioncode=3))
            except Exception as exc:  # timeouts, CRC/LRC errors, bus noise
                last = exc
                time.sleep(0.05 * (attempt + 1))
        raise PIDError(f"No valid reply from CND3 (address {self.address}) on {self.port}: {last}")

    def read(self) -> PIDReading:
        pv_raw, sv_raw = self._read(REG_PV, 2)
        return PIDReading(pv_c=decode_temperature(pv_raw), sv_c=decode_temperature(sv_raw))

    def read_pv(self) -> float:
        return decode_temperature(self._read(REG_PV)[0])

    def control_method(self) -> str:
        """PID / ON-OFF / Manual / Fuzzy (register 1005H)."""
        return CONTROL_METHODS.get(self._read(REG_CONTROL_METHOD)[0], "?")

    def run_state(self) -> str:
        """RUN / STOP / END / HOLD (register 103CH)."""
        return RUN_STATES.get(self._read(REG_RUN_STOP)[0], "?")

    def version(self) -> str:
        raw = self._read(REG_VERSION)[0]
        return f"V{raw >> 8}.{raw & 0xFF:02X}"

    def status(self) -> dict:
        info = {"firmware": self.version()}
        try:
            info["pv_c"] = self.read_pv()
        except PIDSensorError as exc:
            info["pv_c"] = None
            info["sensor_error"] = str(exc)
        info["sv_c"] = decode_temperature(self._read(REG_SV)[0])
        info["output1_percent"] = self._read(REG_OUTPUT1)[0] / 10.0
        info["control"] = CONTROL_METHODS.get(self._read(REG_CONTROL_METHOD)[0], "?")
        info["run_state"] = RUN_STATES.get(self._read(REG_RUN_STOP)[0], "?")
        led = self._read(REG_LED)[0]
        info["unit"] = "°F" if led & 0b1000 else "°C"
        return info

    def close(self) -> None:
        try:
            self.inst.serial.close()
        except Exception:
            pass


def autodetect(
    port: str,
    addresses: tuple[int, ...] = (1,),
    progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict | None:
    """Try the likely serial settings until the controller answers.

    Returns the working settings dict, or None.  Factory default is tried first.
    """
    formats = [(7, "E", 1), (8, "N", 1), (7, "O", 1), (8, "E", 1), (7, "N", 2), (8, "N", 2), (7, "E", 2), (7, "O", 2), (8, "O", 1)]
    for mode in ("ascii", "rtu"):
        for baud in BAUD_RATES:
            for bytesize, parity, stop in formats:
                if mode == "rtu" and bytesize == 7:
                    continue  # RTU needs 8 data bits
                for addr in addresses:
                    if should_stop and should_stop():
                        return None
                    label = f"{mode.upper()} {baud} {bytesize}{parity}{stop} addr {addr}"
                    if progress:
                        progress(label)
                    dev = None
                    try:
                        dev = CND3(port, addr, mode, baud, bytesize, parity, stop, timeout_s=0.25, retries=0)
                        fw = dev.version()
                        return {"mode": mode, "baudrate": baud, "bytesize": bytesize, "parity": parity,
                                "stopbits": stop, "address": addr, "firmware": fw}
                    except Exception:
                        pass
                    finally:
                        if dev is not None:
                            dev.close()
    return None
