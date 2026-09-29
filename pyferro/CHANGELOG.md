# Changelog

Versions follow `MAJOR.MINOR.PATCH`. Each release is tagged `v<version>` in git, and
the same number appears in the window title and in every data-file header
(`# software: pyferro <version>`), so a saved measurement can always be traced back
to the code that produced it. The number is written in exactly one place,
`__version__` in `ferro/__init__.py`; see "Versioning and releases" in
[README.md](README.md).

## Unreleased

### Added
- **Choice of lock-in and multimeter model** on the Instruments tab. The lock-in can be
  an EG&G 5302 (this rig, the default) or an SRS SR830; the multimeter
  an HP 34401A (the default) or a Keithley 199. The choice is saved with the other
  settings, used in simulation, shown by the Test buttons and in the log, and written
  into every data file header (`# lockin_model:`, `# temperature_source:`). The SR830
  and Keithley 199 manuals are in `docs/manuals/`.
- The SR830 is read over GPIB with one query for X, Y, R and θ together. Its overload
  status latches, so an overload between two samples still flags the later one; a
  status read that needed a retry flags the sample too, since the retry may have missed
  the latched bits. It
  answers in volts, so there is no scaling. Its output offset and expand are read with
  every sample and logged when set (including when already set at the start), because the manual does not say whether the values
  read include them.
- The Keithley 199 has no `*IDN?`; its status word identifies it. It is set to ohms on
  every connection, with Zero turned off, because opening the connection resets it to
  its saved defaults. An overload is reported
  as an error, not recorded as a huge resistance, and a reading in the wrong function
  (someone changed it on the front panel) is refused.
- Flag **16** in the data file: temperature discarded just after connecting.
- **The 5302's front-panel set-up is read over GPIB**: reference mode, oscillator level
  and frequency, dynamic reserve, filter, signal input and reference phase. The Test
  button shows them and every data file header records them, so a file says how the
  lock-in was set. The lab manual has a matching 5302 settings table, with how to set
  each one.
- **`pixi run start` updates itself first** with `git pull --ff-only`, so the lab PC's
  clone always runs the latest code. Being offline, a local edit that blocks the pull,
  or a folder that is not a git clone never stops the program starting.
  `pixi run start --skip-deps` starts without updating.

### Fixed
- **The window ran off the right of the screen.** It opened at a fixed 1400 px, wider
  than the lab PC's 1280 px screen, and once recording started the banner's file path
  forced it to about 2600 px, since a window never goes narrower than its widest label.
  It now opens at the screen's size when that is smaller, and the banner has its own
  line and shortens the middle of the path ("…"), with the full path as its tooltip.
- **Heating and cooling were split badly when the temperature wobbles.** The tracker
  used a 60 s slope, and a wobble of a few degrees - the controller's relay switching
  the heater - flipped it on every swing, recolouring the curves and counting a new
  segment each time. It now averages over 120 s and turns only once the average has come
  back 3 °C from the highest (or lowest) point reached. On synthetic runs with ±5 °C
  swings of period 5–120 s it turns exactly once, at the true peak; the old tracker
  turned up to hundreds of times. A turn is recognised a minute or two late, so the
  plot recolours the points back to the peak and the file gets a
  `# Ramp turned to cooling at … °C, t = … s` line. The `direction` column's 0 now
  means "not yet known" only; there is no separate "steady".
- **After Clear plots, nothing new appeared on the plots.** Zooming or dragging a plot
  switches its auto-range off, which pins the view to the old time window; once the old
  points were cleared, every new one landed off-screen, so it looked as if nothing was
  recorded. The data file was unaffected. Clear plots now turns auto-range back on.
- **Temperatures of about −3000 °C at the start of a run** squashed the plot. A status
  word from the controller outside the five fault codes the manual lists (8000H decodes
  to −3276.8 °C) was read as a temperature. Anything below −999.9 °C, lower than any
  input can measure, is now reported as a controller fault instead. And the first 3
  temperatures after the controller or multimeter connects, at the start or on a
  reconnect, are discarded: `nan` in the file with flag 16, left out of the plot.
- **Connecting to the lock-in, and the Test button, timed out.** The app asked for a
  query's reply the instant it had sent the query; the 5302 had not finished parsing it,
  so the reply came late and landed on the next query (`SEN answered 5302`), after which
  every attempt failed. The lock-in is now given 50 ms between the query and the read.
  Against the real instrument the connect-and-test path went from 0 of 10 to 15 of 15,
  and 60 of 60 samples were read with no retries.

## 1.0.2 — 2026-09-17

### Fixed
- **Y was scaled wrongly whenever Expand was on.** Expand multiplies the gain of the x
  channel only (manual sections 4 and 9), but Y was divided by ten as well, so Y read
  ten times too small. Files recorded with the EX indicator lit have Y wrong by that
  factor; X was right.
- A dropped reply to `EX` no longer switches expand detection off for the rest of the
  connection. That fallback assumed expand was off, which would have read X ten times
  too large without a warning; the sample is now recorded as missing instead.
- The lock-in's terminator search no longer retries each attempt, so a silent
  instrument no longer holds up Stop and the reading loop for about a minute.
- The offline installer could not run on a clean Windows machine. Three separate
  faults, all in the packaging rather than the program:
  - The Windows launchers shipped with Unix line endings, which `cmd.exe` mis-parses.
    They are now written with CRLF, `.gitattributes` keeps them that way, and a test
    fails if a bare newline gets back in.
  - `pixi-unpack.exe` is an MSVC build and needs `VCRUNTIME140.dll`, which a bare
    Windows install does not have and which otherwise means an admin-rights
    redistributable. The bundle now carries that DLL — taken from the same
    environment it unpacks — next to the unpacker, where Windows looks first.
  - `INSTALL.bat` reported `The system cannot find the path specified` when it was
    run from inside Explorer's zip viewer, which copies only that one file. It now
    checks for each piece it needs and says which is missing, listing what it did
    find, and catches the network-share case that `cd /d` cannot handle.
- Plot colours no longer misrepresent the ramp. Points recorded before the ramp
  direction is established, and any hold at setpoint, were carried forward as the
  previous direction — so the start of every run was drawn as "heating" whatever it was
  doing. Steady readings now have their own neutral curve, matching the `direction`
  column in the data file.
- The heating and cooling branches cover the same temperature range, so the cooling
  curve was drawn over the heating one and hid it. Cooling is now dashed and slightly
  transparent, leaving both readable where they overlap.
- Temperature vs time is coloured by ramp direction as well — the one plot where the
  branches cannot overlap.

## 1.0.1 — 2026-09-16

### Fixed
- Recording on macOS: the default save folder is now `~/FerroData`. `~/Documents` is
  protected by macOS privacy, and recording there failed with "Operation not permitted".
- The save-folder check no longer refuses a folder that allows writing but not deleting,
  uses a uniquely named probe file, and its message names the macOS privacy setting.
- Dark mode: the readout tiles and the log kept their text colour from the system theme,
  giving white text on the light tiles. The window now follows the system theme, and the
  tiles, the log panel and the plots use a matching light or dark colour set.

### Added
- A session log, written from start-up to `~/.ferro/logs/pyferro_<start>.log`, holding
  every message the log panel shows — including set-up problems from before recording
  began. Each data file names it in its header (`# session_log:`) and gets a copy beside
  it (`<name>.log`) when the recording closes. The twenty newest logs are kept.

- Events that change what the numbers mean are now logged **and written into the data
  file** as `#` comment lines: lock-in sensitivity, expand and overload changes, time
  constant and reference frequency changes, controller mode or RUN/STOP changes,
  reading interval, ΔT threshold, chamber limit, temperature source, and edits to the
  run name, operator, drive and notes.
- Instruments now report recovery ("answering again after 12 s") and reconnection
  attempts, not only the first failure.
- The header of every data file names the port each instrument used
  (`# connection_lockin:`, `# connection_pid:`), and the same lines are logged when a run
  starts, so files from different rigs can be told apart.
- The session log records the machine, Python and library versions at start-up, and a
  closing line, so a log that ends abruptly means a crash rather than a normal exit.

### Changed
- *Temperature from* can be changed while monitoring and is locked only while recording,
  so a single file keeps one temperature source. The multimeter is opened on demand when
  it is selected during a run.

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
