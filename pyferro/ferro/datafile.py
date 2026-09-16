"""Crash-safe data files.

Standard format: ``#`` header lines (sample, lock-in settings, column names)
followed by tab-separated numbers.  Loads directly with
``numpy.loadtxt(path)`` or ``pandas.read_csv(path, sep="\\t", comment="#")``.
The first three columns are T, X, Y exactly like the old LabVIEW files.

Legacy format: only T, X, Y in ``%.5e`` with no header (readable by plot.vi);
metadata goes to a ``.json`` file next to it.

Every row is flushed and fsync'd, so a crash or power cut loses at most the
row being written.  Existing files are never overwritten.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

COLUMNS = [
    ("T_C", "sample temperature used for plots (degC)"),
    ("X_V", "lock-in X (V rms)"),
    ("Y_V", "lock-in Y (V rms)"),
    ("time_s", "seconds since start"),
    ("R_V", "sqrt(X^2+Y^2) (V)"),
    ("theta_deg", "atan2(Y, X) (deg)"),
    ("SV_C", "controller setpoint (degC)"),
    ("PV_C", "controller temperature (degC)"),
    ("T_dmm_C", "multimeter Pt100 temperature (degC)"),
    ("sens_V", "lock-in full-scale sensitivity (V)"),
    ("direction", "+1 heating, -1 cooling, 0 steady"),
    ("segment", "ramp segment number"),
    ("flags", "bitmask: 1 lock-in overload, 2 lock-in error, 4 controller error, 8 multimeter error"),
]
LEGACY_COLUMNS = ["T_C", "X_V", "Y_V"]

FLAG_LOCKIN_OVERLOAD = 1
FLAG_LOCKIN_ERROR = 2
FLAG_PID_ERROR = 4
FLAG_DMM_ERROR = 8


def check_writable(folder: str | Path) -> None:
    """Raise OSError if a data file could not be created in ``folder``.

    The probe file is uniquely named (so two copies of the program cannot collide)
    and a failure to delete it afterwards is ignored: recording must not be blocked
    by a folder that allows writing but not cleanup.
    """
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Cannot create the folder {folder}: {exc}{_permission_hint(exc)}") from exc

    probe = folder / f".ferro_write_test_{os.getpid()}"
    try:
        probe.write_text("ok")
    except OSError as exc:
        raise OSError(f"Cannot write into {folder}: {exc}{_permission_hint(exc)}") from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


def _permission_hint(exc: OSError) -> str:
    if exc.errno not in (errno.EPERM, errno.EACCES):
        return ""
    if sys.platform == "darwin":
        return ("\n\nmacOS is blocking access. Either choose a folder outside "
                "Documents/Desktop/Downloads, or grant the program access in "
                "System Settings → Privacy & Security → Files and Folders.")
    return "\n\nChoose a different folder, or check that the drive is connected and not read-only."


def safe_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return text.strip("._") or "run"


def unique_path(folder: str | Path, sample: str, when: datetime | None = None) -> Path:
    folder = Path(folder)
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    base = f"{safe_name(sample)}_{stamp}"
    path = folder / f"{base}.txt"
    n = 2
    while path.exists() or path.with_suffix(".json").exists():
        path = folder / f"{base}_{n}.txt"
        n += 1
    return path


def _fmt(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "nan"
    if isinstance(value, int):
        return str(value)
    return f"{value:.5e}"


class DataWriter:
    def __init__(self, path: str | Path, metadata: dict, legacy: bool = False) -> None:
        self.path = Path(path)
        self.legacy = legacy
        self.rows = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "x", encoding="utf-8", newline="\n")
        meta = {"created": datetime.now().isoformat(timespec="seconds"), **metadata}
        if legacy:
            meta["columns"] = LEGACY_COLUMNS
            self.path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))
        else:
            for key, value in meta.items():
                self._fh.write(f"# {key}: {value}\n")
            for i, (name, desc) in enumerate(COLUMNS, 1):
                self._fh.write(f"# column {i}: {name} - {desc}\n")
            self._fh.write("# " + "\t".join(name for name, _ in COLUMNS) + "\n")
            self._sync()

    def _sync(self) -> None:
        self._fh.flush()
        try:
            os.fsync(self._fh.fileno())
        except OSError:
            pass

    def comment(self, text: str) -> None:
        """Record an event in the file itself, e.g. a setting changed mid-run.

        Comment lines are ignored by numpy.loadtxt and pandas (comment="#"), so they
        are safe to interleave with the data.
        """
        if self.legacy:  # the old format must stay numbers only
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self._fh.write(f"# {stamp} {text}\n")
        self._sync()

    def write(self, row: dict) -> None:
        names = LEGACY_COLUMNS if self.legacy else [name for name, _ in COLUMNS]
        self._fh.write("\t".join(_fmt(row.get(name)) for name in names) + "\n")
        self._sync()
        self.rows += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._sync()
            self._fh.close()
