"""One log file per program run.

It starts with the program, not with a recording: the messages that explain a
failed run - a controller that never answered, an auto-detect that found nothing,
a save folder that was refused - are usually written while setting up, long before
anyone presses Record. The on-screen log panel is transient, so everything it shows
is also appended here.

Logging must never break a measurement: if the file cannot be created or written,
every call quietly does nothing.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

KEEP_FILES = 20

_current: "SessionLog | None" = None


def default_folder() -> Path:
    base = os.environ.get("FERRO_CONFIG_DIR") or (Path.home() / ".ferro")
    return Path(base) / "logs"


class SessionLog:
    def __init__(self, folder: str | Path | None = None) -> None:
        self.path: Path | None = None
        self._fh = None
        folder = Path(folder) if folder is not None else default_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.path = folder / f"pyferro_{datetime.now():%Y%m%d_%H%M%S}.log"
            self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
            _prune(folder, KEEP_FILES)
        except OSError:
            self.path, self._fh = None, None

    def write(self, level: str, message: str) -> None:
        if self._fh is None:
            return
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            for line in message.splitlines() or [""]:
                self._fh.write(f"{stamp}  {level.upper():<7} {line}\n")
            self._fh.flush()
        except OSError:
            pass

    def copy_to(self, target: str | Path) -> bool:
        """Best-effort copy of the log so far, e.g. next to a finished data file."""
        if self._fh is None or self.path is None:
            return False
        try:
            self._fh.flush()
            shutil.copyfile(self.path, target)
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def _prune(folder: Path, keep: int) -> None:
    try:
        logs = sorted(folder.glob("pyferro_*.log"))
        for old in logs[:-keep] if len(logs) > keep else []:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def start(folder: str | Path | None = None) -> SessionLog:
    global _current
    _current = SessionLog(folder)
    return _current


def current() -> "SessionLog | None":
    return _current


def write(level: str, message: str) -> None:
    if _current is not None:
        _current.write(level, message)


def path() -> str | None:
    return str(_current.path) if _current is not None and _current.path else None
