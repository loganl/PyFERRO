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

import json
import math
import os
import re
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

    def write(self, row: dict) -> None:
        names = LEGACY_COLUMNS if self.legacy else [name for name, _ in COLUMNS]
        self._fh.write("\t".join(_fmt(row.get(name)) for name in names) + "\n")
        self._sync()
        self.rows += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._sync()
            self._fh.close()
