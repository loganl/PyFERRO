"""Acquisition engine: owns the instruments and the measurement loop.

Runs in a background thread and reports through plain callbacks, so it is
GUI-independent (the Qt window wraps the callbacks in signals).

Robustness rules:
* A failed instrument read never stops the run - that value becomes NaN, a flag
  bit is set in the file, and the instrument is reopened after repeated failures.
* Recording can be switched on/off while monitoring (like the old "Save" button);
  each recording goes to a new, never-overwritten file.
"""

from __future__ import annotations

import math
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from . import __version__
from .analysis import DirectionTracker
from .config import AppConfig
from .datafile import (
    FLAG_DMM_ERROR,
    FLAG_LOCKIN_ERROR,
    FLAG_LOCKIN_OVERLOAD,
    FLAG_PID_ERROR,
    DataWriter,
    unique_path,
)
from .instruments.cnd3 import CND3, PIDError
from .instruments.hp34401a import HP34401A
from .instruments.lockin5302 import Lockin5302
from .instruments import simulated
from .transports import SerialTransport, TransportError, VisaTransport

NAN = float("nan")
REOPEN_AFTER_FAILURES = 3
REOPEN_BACKOFF_S = 5.0


# --- instrument factories (also used by the GUI "Test" buttons) ------------
def open_lockin(cfg: AppConfig) -> Lockin5302:
    c = cfg.lockin
    if cfg.simulate:
        return Lockin5302(simulated.SimLockinTransport(simulated.shared_sample()))
    if c.interface == "serial":
        if not c.serial_port:
            raise TransportError("No RS-232 port selected for the lock-in")
        return Lockin5302(SerialTransport(c.serial_port, baudrate=c.baudrate, timeout_s=c.timeout_s))
    return Lockin5302(VisaTransport(c.resource, timeout_s=c.timeout_s))


def open_pid(cfg: AppConfig) -> CND3:
    c = cfg.pid
    if cfg.simulate:
        return CND3("SIM", instrument=simulated.SimModbusInstrument(simulated.shared_sample()))
    if not c.port:
        raise PIDError("No RS-485 port selected for the CND3 controller")
    return CND3(c.port, c.address, c.mode, c.baudrate, c.bytesize, c.parity, c.stopbits, c.timeout_s)


def open_dmm(cfg: AppConfig) -> HP34401A:
    c = cfg.dmm
    if cfg.simulate:
        return HP34401A(simulated.SimDMMTransport(simulated.shared_sample()), c.mode, c.r0)
    return HP34401A(VisaTransport(c.resource, timeout_s=3.0), c.mode, c.r0)


@dataclass
class InstrumentSlot:
    name: str
    opener: Callable[[], object]
    device: object | None = None
    failures: int = 0
    next_attempt: float = 0.0
    state: str = "off"  # off | ok | error
    message: str = ""

    def get(self):
        if self.device is None and time.monotonic() >= self.next_attempt:
            try:
                self.device = self.opener()
            except Exception as exc:
                self.next_attempt = time.monotonic() + REOPEN_BACKOFF_S
                raise
        if self.device is None:
            raise TransportError(f"{self.name}: waiting to reconnect")
        return self.device

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= REOPEN_AFTER_FAILURES and self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None
            self.next_attempt = time.monotonic() + REOPEN_BACKOFF_S
            self.failures = 0

    def close(self) -> None:
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None


class Acquisition:
    def __init__(
        self,
        cfg: AppConfig,
        on_sample: Callable[[dict], None] = lambda row: None,
        on_log: Callable[[str, str], None] = lambda level, msg: None,
        on_status: Callable[[str, str, str], None] = lambda name, state, msg: None,
        on_recording: Callable[[str | None], None] = lambda path: None,
    ) -> None:
        self.cfg = cfg
        self.on_sample, self.on_log, self.on_status, self.on_recording = on_sample, on_log, on_status, on_recording
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._record_request: bool | None = None
        self._req_lock = threading.Lock()
        self.writer: DataWriter | None = None
        self.tracker = DirectionTracker()
        self.slots = {
            "lockin": InstrumentSlot("Lock-in", lambda: open_lockin(cfg)),
            "pid": InstrumentSlot("CND3", lambda: open_pid(cfg)),
        }
        if cfg.dmm.enabled or cfg.run.temp_source == "dmm":
            self.slots["dmm"] = InstrumentSlot("Multimeter", lambda: open_dmm(cfg))
        self._last_logged_t = NAN
        self._over_temp = False
        self._t0 = 0.0

    # --- control (call from any thread) ---------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recording(self) -> bool:
        return self.writer is not None

    def start(self, record: bool = False) -> None:
        if self.running:
            return
        self._stop.clear()
        self._record_request = True if record else None
        self._thread = threading.Thread(target=self._run, name="ferro-acquisition", daemon=True)
        self._thread.start()

    def set_recording(self, on: bool) -> None:
        with self._req_lock:
            self._record_request = on

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # --- loop ------------------------------------------------------------
    def _set_status(self, key: str, state: str, message: str = "") -> None:
        slot = self.slots[key]
        if (state, message) != (slot.state, slot.message):
            slot.state, slot.message = state, message
            self.on_status(key, state, message)

    def _read(self, key: str, fn: Callable[[object], object]):
        slot = self.slots[key]
        try:
            value = fn(slot.get())
            slot.failures = 0
            self._set_status(key, "ok")
            return value
        except Exception as exc:
            if slot.state != "error":
                self.on_log("warning", f"{slot.name}: {exc}")
            self._set_status(key, "error", str(exc))
            slot.failed()
            return None

    def _run(self) -> None:
        self._t0 = time.monotonic()
        self.on_log("info", "Acquisition started" + (" (SIMULATION)" if self.cfg.simulate else ""))
        next_tick = self._t0
        try:
            while not self._stop.is_set():
                try:
                    self._handle_record_request()
                    row = self._sample()
                    self.on_sample(row)
                    self._maybe_write(row)
                except Exception:
                    self.on_log("error", "Unexpected error (acquisition continues):\n" + traceback.format_exc())
                interval = max(0.05, float(self.cfg.run.interval_s))
                next_tick += interval
                delay = next_tick - time.monotonic()
                if delay < 0:  # fell behind (slow instrument) - don't try to catch up
                    next_tick = time.monotonic()
                    delay = 0
                self._stop.wait(delay)
        finally:
            self._close_writer()
            for slot in self.slots.values():
                slot.close()
            self.on_log("info", "Acquisition stopped")

    def _sample(self) -> dict:
        row = {"time_s": time.monotonic() - self._t0, "flags": 0}

        pid = self._read("pid", lambda d: d.read())
        row["PV_C"] = pid.pv_c if pid else NAN
        row["SV_C"] = pid.sv_c if pid else NAN
        if pid is None:
            row["flags"] |= FLAG_PID_ERROR

        row["T_dmm_C"] = NAN
        if "dmm" not in self.slots and (self.cfg.dmm.enabled or self.cfg.run.temp_source == "dmm"):
            # The multimeter was switched on (or selected) after the run started.
            self.slots["dmm"] = InstrumentSlot("Multimeter", lambda: open_dmm(self.cfg))
        if "dmm" in self.slots:
            t_dmm = self._read("dmm", lambda d: d.read_celsius())
            if t_dmm is None:
                row["flags"] |= FLAG_DMM_ERROR
            else:
                row["T_dmm_C"] = t_dmm

        li = self._read("lockin", lambda d: d.read())
        if li is None:
            row.update(X_V=NAN, Y_V=NAN, R_V=NAN, theta_deg=NAN, sens_V=NAN, percent_fs=NAN)
            row["flags"] |= FLAG_LOCKIN_ERROR
        else:
            row.update(X_V=li.x_v, Y_V=li.y_v, R_V=li.r_v, theta_deg=li.theta_deg,
                       sens_V=li.full_scale_v, percent_fs=li.percent_fs)
            if li.overloaded:
                row["flags"] |= FLAG_LOCKIN_OVERLOAD

        row["T_C"] = row["T_dmm_C"] if self.cfg.run.temp_source == "dmm" else row["PV_C"]
        row["direction"] = self.tracker.update(row["time_s"], row["T_C"])
        row["segment"] = self.tracker.segment
        row["slope_c_per_min"] = self.tracker.slope_c_per_min

        limit = self.cfg.run.max_temp_c
        t = row["T_C"]
        if not math.isnan(t):
            if t > limit and not self._over_temp:
                self.on_log("error", f"Temperature {t:.1f} °C is above the {limit:.0f} °C chamber limit!")
            self._over_temp = t > limit
        row["over_temp"] = self._over_temp
        return row

    def _maybe_write(self, row: dict) -> None:
        if self.writer is None:
            return
        min_dt = self.cfg.run.min_delta_t
        t = row["T_C"]
        if min_dt > 0 and not math.isnan(t) and not math.isnan(self._last_logged_t):
            if abs(t - self._last_logged_t) < min_dt:
                return
        try:
            self.writer.write(row)
            if not math.isnan(t):
                self._last_logged_t = t
        except OSError as exc:
            self.on_log("error", f"Could not write data file ({exc}); recording stopped. Check the disk/USB drive.")
            self._close_writer()

    def _handle_record_request(self) -> None:
        with self._req_lock:
            request, self._record_request = self._record_request, None
        if request is True and self.writer is None:
            self._open_writer()
        elif request is False and self.writer is not None:
            self._close_writer()

    def _metadata(self) -> dict:
        run = self.cfg.run
        meta = {
            "software": f"pyferro {__version__}",
            "sample": run.sample,
            "operator": run.operator,
            "drive": run.drive,
            "notes": run.notes.replace("\n", " | "),
            "temperature_source": "CND3 controller PV" if run.temp_source == "pid" else "HP34401A Pt100",
            "interval_s": run.interval_s,
            "min_delta_T_C": run.min_delta_t,
            "simulation": self.cfg.simulate,
        }
        for key, label in (("lockin", "lockin"), ("pid", "controller")):
            slot = self.slots[key]
            try:
                dev = slot.get()
                info = dev.settings() if key == "lockin" else dev.status()
                for k, v in info.items():
                    meta[f"{label}_{k}"] = v
            except Exception as exc:
                meta[f"{label}_error"] = str(exc)
        return meta

    def _open_writer(self) -> None:
        run = self.cfg.run
        try:
            path = unique_path(run.output_dir, run.sample or "sample")
            self.writer = DataWriter(path, self._metadata(), legacy=run.legacy_format)
            self._last_logged_t = NAN
            self.on_log("info", f"Recording to {path}")
            self.on_recording(str(path))
        except Exception as exc:
            self.writer = None
            self.on_log("error", f"Could not create data file in {run.output_dir}: {exc}")
            self.on_recording(None)

    def _close_writer(self) -> None:
        if self.writer is not None:
            path, rows = self.writer.path, self.writer.rows
            try:
                self.writer.close()
            finally:
                self.writer = None
            self.on_log("info", f"Saved {rows} rows to {path}")
            self.on_recording(None)
