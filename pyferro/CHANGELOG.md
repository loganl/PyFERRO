# Changelog

Versions follow `MAJOR.MINOR.PATCH`. Each release is tagged `v<version>` in git, and
the same number appears in the window title and in every data-file header
(`# software: pyferro <version>`), so a saved measurement can always be traced back
to the code that produced it. The number is written in exactly one place,
`__version__` in `ferro/__init__.py`; see "Versioning and releases" in
[README.md](README.md).

## 1.0.0 — 2026-09-16

First Python release, replacing the LabVIEW routine `FERRO v.2.vi`.

### Added
- Acquisition of lock-in X/Y and sample temperature with live plots, recording that
  can be switched on and off during a run, and files that are never overwritten.
- EG&G 5302 lock-in driver over GPIB or RS-232, reading the sensitivity (and expand)
  back from the instrument before every reading.
- Omega CND3 (Delta DT340) temperature-controller driver over RS-485 Modbus
  ASCII/RTU, read-only, with auto-detection of the serial settings.
- Optional HP 34401A multimeter as a second Pt100 thermometer (IEC 60751 conversion).
- Simulation mode for practice and testing without hardware.
- Data files with a metadata header, plus columns for time, R/θ, setpoint,
  ramp direction, segment number and per-instrument error flags; an option writes the
  old three-column LabVIEW format instead.
- Offline Windows bundle built with pixi and pixi-pack, including the instrument
  manuals and the wiring diagram.
- Tests: instrument drivers, wire-level protocol tests over a virtual serial port
  (checked against the frames printed in the manuals), data files and the GUI.

### Fixed compared with the LabVIEW routine
- Lock-in readings are scaled using the sensitivity read from the instrument, instead
  of a stored value that could drift out of step (the likely cause of X values stuck
  at 1.00 × 10⁻⁷ V in the 2025 BaTiO₃ data).
- A failed instrument read no longer stops a run: the value is recorded as `nan`,
  flagged, and the connection is reopened automatically.
