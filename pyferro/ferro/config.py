"""Persistent settings (JSON in the user's home folder)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_path() -> Path:
    base = os.environ.get("FERRO_CONFIG_DIR") or (Path.home() / ".ferro")
    return Path(base) / "settings.json"


def default_data_dir() -> str:
    # macOS protects ~/Documents with its privacy system (TCC): a process without
    # that permission gets "Operation not permitted" when it tries to record.
    # The plain home folder is not protected, so use it there.
    if sys.platform == "darwin":
        return str(Path.home() / "FerroData")
    return str(Path.home() / "Documents" / "FerroData")


@dataclass
class LockinConfig:
    interface: str = "visa"  # "visa" (GPIB) or "serial" (RS-232)
    resource: str = "GPIB0::12::INSTR"
    serial_port: str = ""
    baudrate: int = 9600
    timeout_s: float = 2.0


@dataclass
class PIDConfig:
    port: str = ""
    address: int = 1
    mode: str = "ascii"
    baudrate: int = 9600
    bytesize: int = 7
    parity: str = "E"
    stopbits: int = 1
    timeout_s: float = 0.5


@dataclass
class DMMConfig:
    enabled: bool = False
    resource: str = "GPIB0::24::INSTR"
    mode: str = "pt100"
    r0: float = 100.0


@dataclass
class RunConfig:
    sample: str = ""
    operator: str = ""
    notes: str = ""
    drive: str = ""
    output_dir: str = field(default_factory=default_data_dir)
    interval_s: float = 1.0
    min_delta_t: float = 0.0
    legacy_format: bool = False
    temp_source: str = "pid"  # "pid" or "dmm"
    max_temp_c: float = 160.0


@dataclass
class AppConfig:
    lockin: LockinConfig = field(default_factory=LockinConfig)
    pid: PIDConfig = field(default_factory=PIDConfig)
    dmm: DMMConfig = field(default_factory=DMMConfig)
    run: RunConfig = field(default_factory=RunConfig)
    simulate: bool = False

    def to_dict(self) -> dict:
        # Simulation is a per-launch mode, not a saved preference. Running
        # --simulate once used to write simulate=true here, and every later run
        # came up simulated until someone noticed the banner.
        data = asdict(self)
        data.pop("simulate", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        cfg = cls()
        for section in ("lockin", "pid", "dmm", "run"):
            target = getattr(cfg, section)
            values = data.get(section, {}) if isinstance(data, dict) else {}
            for f in fields(target):
                if f.name in values:
                    default = getattr(target, f.name)
                    try:
                        setattr(target, f.name, type(default)(values[f.name]))
                    except (TypeError, ValueError):
                        pass
        cfg.simulate = False  # never restored from disk; see to_dict
        return cfg


def load() -> AppConfig:
    try:
        return AppConfig.from_dict(json.loads(config_path().read_text()))
    except Exception:
        return AppConfig()


def save(cfg: AppConfig) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg.to_dict(), indent=2))
        tmp.replace(path)
    except OSError:
        pass
