# PyFERRO — working notes for Claude

Data acquisition for the PHY445 phase-transition lab: a lock-in amplifier measures a
sample's dielectric response while a PID controller ramps its temperature through a
transition. Replaces `FERRO v.2.vi`, a LabVIEW 2009 routine. The repo also holds the
lab manual (`ptmanual/`).

## Documentation — keep it in step with every change

| File | What it is |
|---|---|
| `pyferro/README.md` | **the** documentation: install, wiring, taking data, file format, troubleshooting, protocols, code layout, tests |
| `pyferro/docs/drivers-and-daq.md` | teaching walkthrough of transports, drivers and the loop (the one extra doc file, added at the user's request) |
| `pyferro/CHANGELOG.md` | every user-visible change goes under `## Since 1.0.2` |
| `README.md` (root) | short overview pointing at the above |
| `ptmanual/main.tex` | the lab manual; its PyFERRO appendix (`app:pyferro`) and 5302 table describe the program |
| module docstrings | each driver's docstring lists the commands it uses |

Don't add more doc files; extend these. A code change that alters behaviour, a
message, a command or a file column needs the matching README/CHANGELOG (and, for
drivers or the loop, drivers-and-daq.md) edit in the same commit.

## Layout

| Path | What |
|---|---|
| `pyferro/ferro/` | the program — `gui/` (`main_window.py`, `setup_panel.py` = Instruments tab, `lockin_panel.py` = Lock-in tab), `acquisition.py` (loop, `LOCKIN_MODELS`/`DMM_MODELS`), `instruments/`, `transports.py`, `datafile.py`, `analysis.py`, `sessionlog.py`, `config.py` |
| `pyferro/tools/gpib_check.py` | GPIB diagnostics for the 5302 (asks `ID`); `pixi run gpib` |
| `pyferro/tools/update.py` | `git pull --ff-only` run by `pixi run start`; never blocks the start |
| `pyferro/tests/` | `test_core.py` (drivers vs fakes, data files, loop), `test_protocols.py` (serial/Modbus over a pty), `test_gui.py` (pytest-qt), `test_version.py` |
| `pyferro/docs/manuals/` | instrument manuals — **read these before guessing at instrument behaviour** |
| `ptmanual/` | LaTeX lab manual. Its 5302 table must match `CAPACITANCE_SETUP` in `lockin5302.py`; `main.pdf`/`main-tagged.pdf` are committed, so rebuild them (`make`, `make tagged`; LuaLaTeX, TeX Live ≥ 2024) after editing `main.tex` |

There is **no packaging**: the offline Windows bundle, `packaging/` and `release.sh`
were removed at the user's request (2026-09-30). Don't bring them back.

## Commands

Run from `pyferro/`:

```bash
pixi run simulate            # GUI against simulated instruments
pixi run start               # git pull --ff-only (tools/update.py), then real instruments
pixi run -e test test        # full suite (QT_QPA_PLATFORM=offscreen on a headless box)
pixi run gpib                # diagnose the 5302's GPIB link
```

Use the pixi environment, not pip into the system Python — the user's preference.
Two test skips are expected on Windows: the pty protocol module and the chmod
writability test.

### In a Claude Code cloud session

- pixi is not preinstalled and `pixi.sh` is blocked by the network policy. The binary
  from GitHub releases works:
  `curl -fsSL https://github.com/prefix-dev/pixi/releases/latest/download/pixi-x86_64-unknown-linux-musl.tar.gz | tar -xz -C ~/.pixi/bin`
  (after `mkdir -p ~/.pixi/bin`), then `~/.pixi/bin/pixi install -e test`.
- Four tests fail in that container and pass on real machines: the chmod
  writability test (the container runs as root) and three `test_protocols.py` CND3
  tests (its pty refuses 7E1 framing: `termios.error: (22, 'Invalid argument')`).
  Anything else failing is real.
- No TeX Live and CTAN is blocked, so the lab-manual PDFs cannot be rebuilt there; say
  so and leave `make` to the user.

## Conventions

- **No releases, no tags** (user, 2026-09-30): the lab PC runs `main`. Don't bump the
  version, add release sections, tag, or reintroduce release tooling. `__version__` in
  `pyferro/ferro/__init__.py` stays at 1.0.2 (window title, data-file header); a test
  fails if a second copy of it appears. Old tags `v1.0.0`/`v1.0.1` remain on GitHub.
- **Commits**: end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Pushing**: push work the user asked for without asking first; they pull it on the
  lab PC. Sessions work on `claude/...` branches; the lab PC pulls `main`, so work
  reaches the lab only once merged.
- The lab PC has internet and a git clone at `C:\PyFERRO`, started with
  `pixi run start` (which pulls first). Windows 10/11, 1280 px wide screen.
- On the lab PC the folder is `PyFERRO` on disk but tracked as `pyferro`: a **new**
  file shows as `?? PyFERRO/...`, and neither `git add pyferro` nor
  `git add pyferro/tools/new.py` stages it (adding it as `PyFERRO/...` would split the
  folder in two on case-sensitive systems). Stage it under the tracked name:
  `git update-index --add --cacheinfo "100644,$(git hash-object -w PyFERRO/x/new.py),pyferro/x/new.py"`,
  then check `git status` shows `A  pyferro/...` and nothing untracked.

## The rig, as confirmed on hardware

| | setting |
|---|---|
| EG&G 5302 lock-in | NI GPIB-USB-HS, **PAD 12**, terminator CR |
| Omega CND3 controller | RS-485 via Dtech FTDI adapter, Modbus ASCII, address 1, 9600 7E1, **read-only** |
| CND3 wiring | terminal **13 = D−**, **14 = D+**; probe on 10/11/12 |
| HP 34401A | optional second thermometer, GPIB 24. On the bus 2026-09-29 but set to DC volts (`FUNC?` → `"VOLT"`); the driver does not set the function, so it needs 4-wire Ω from the front panel |
| SRS SR830 lock-in | GPIB **8**, s/n 36537, fw 1.04. Works through the app (10/10 Test, 60/60 samples, a recorded run), 2026-09-29 |
| Keithley 199 | GPIB **6**. Talks through the app 2026-09-29 (status word `1991…`, set to ohms, no errors) but read `OOHM+9.999999E+9` — open circuit; `U5X` → `RF=0` = **front** inputs selected (the FRONT/REAR switch is mechanical, not settable over GPIB) |

Lock-in and multimeter models are chosen on the Instruments tab (`LOCKIN_MODELS`,
`DMM_MODELS` in `acquisition.py`). The EG&G 5301A was removed at the user's request —
don't add it back.

## Things learned the hard way — don't re-derive these

- The 5302 predates SCPI. Its identify command is `ID`, not `*IDN?`; NI MAX reporting
  "did not respond to a \*IDN? query" during a scan is normal and means it *was* found.
- No vendor or community driver exists for the 5302 — not NI's driver network, not
  pymeasure. `lockin5302.py` is the driver. The manual is the only authority.
- The TERMINATOR on the instrument's COMM-I/O → GPIB screen is the **input**
  terminator. The output terminator is set separately (manual §8.5), so all four
  CR/CRLF pairings occur. `probe_terminations` finds the working pair.
- **Expand (`EX`) multiplies the x channel only** (manual §4 and §9). Y keeps its full
  scale. Never assume expand is off when `EX` fails — lose the sample instead.
- SEN is 0–21 and XTC 0–18 (manual tables 9-16, 9-19). A reply outside that range means
  replies are out of step with commands, not an unknown range.
- The status byte (§8.7) names a fault after a rejected command: bit 1 invalid command,
  2 parameter error, 3 reference unlock, 4 overload, 7 data available. Reference unlock
  is expected with no sample connected.
- **Serial-polling for command complete after every exchange breaks this instrument**,
  even though §8.7 describes it. A 50 ms gap between commands is what works.
- **Wait 50 ms between writing a query and reading its reply** (`reply_delay_s`, pyvisa's
  `query(delay=)`). Reading immediately makes the reply late, so it lands on the next
  query - `SEN answered 5302`, `XTC answered 21`. Measured through the app's own
  connect + Test path against the real lock-in: **0/10 without, 15/15 with**, and 60/60
  acquisition samples with no retries. This, not the terminator and not (mainly) the
  cable, was the timeouts on `ID` (2026-09-24).
- Raw, the reply to `ID` is `5302\r` - a bare CR, with EOI. "Write CR, read CRLF" also
  answers, but only because EOI ends the read (pyvisa warns the string lacks its
  terminator). Read CR is correct; do not reorder `TERMINATIONS` for it.
- A reply whose query was abandoned stays queued and lands on a later query
  (`b'1\r5302\r'`, or `21` where `ID` was expected). **Device Clear does not flush it.**
  Draining after the clear at open was measured against not draining, with a stale reply
  queued: it was worse (first query right 3/8 vs 6/8). Don't add it. Killing a script
  mid-query is what leaves these behind.
- Settings read on 2026-09-24 with nothing connected: SEN 21 (1 V), EX on, XTC 8, IE 2
  (external reference), FRQ 1 mHz, `XY` = `0 0` as one line with a space delimiter.
- "-3000 °C" on the first temperatures of a run (reported 2026-09-29) is taken to be a
  CND3 status word outside the manual's 8002H–8007H list: `8000H` decodes to -3276.8.
  **Inferred, not caught on the wire** — COM5 was held by the running app. Now any
  value below -999.9 is a fault, and the first 3 temperatures after each connect are
  discarded (flag 16). If spikes persist, log the raw PV word to confirm the cause.
- The recorded temperature wobbles by degrees (40.5 → 44.5 → 40.6 °C in 3 s; the user
  reports ±5 °C). A slope-based heating/cooling split flips on every swing;
  `DirectionTracker` averages 120 s and turns on a 3 °C band from the extreme instead.
  Don't go back to a slope threshold. The wobble was first put down to the relay
  cycling the heater — **probably wrong**: see the next point.
- **The CND3's PV reading is faulty (2026-09-29 ~15:00).** In STOP, outputs 0 %, OUT1
  light off, PV read 204 → 219 → 137 → 207 → 207 → 123 °C at 4 s intervals — no
  chamber moves like that. Suspect the Pt100 wiring on terminals 10/11/12 or noise
  pickup. It may also explain the wobble, the "keeps heating past the setpoint"
  reports and the -3000 °C spikes. Fix the probe before trusting any PV, and don't
  re-diagnose those symptoms from PV data until it reads steadily.
- An unpowered instrument anywhere on the GPIB chain holds NRFD/NDAC and stalls the
  bus, which also makes a scan "find" a device that never answers.
- `ibic` (MAX → Tools → NI-488.2 → Interactive Control) needs no admin rights, unlike
  MAX's board property pages. `ibln <pad> 0` asks whether anything is listening,
  which is the lowest-level question available.

## Current state of the hardware bring-up

The controller has worked, but did not answer on COM5 on 2026-09-24 or 2026-09-29
("no answer" at the saved settings) — check it is powered and the adapter is seated
before suspecting the code. The lock-in now connects and reads through the app (15/15
Test-button connects, 60/60 samples, 2026-09-24) since the reply delay above went in.
The link may still be a little marginal: a few connects took 2-5 s (a retry inside),
and one reply once came back as `55302`, a doubled byte on the bus. Ruled out: address
(PAD 12 confirmed), terminator, NI driver install, the powered-off multimeter, and the
open sequence (a bisect of it failed on different lines each run).

If it degrades again the remaining suspects are physical: connector screws, cable, the
adapter's USB power (the vendor's fix for random lock-ups is an externally powered hub,
not no hub), or ageing transceivers in the 5302. The transport retries twice.
`pixi run gpib` reports a success rate over twenty queries — use that to tell whether a
reseat or a cable swap actually helped, rather than a single test. It opens a fresh
connection per query, like a program start, with the same reply delay as the app.

Don't respond to intermittent failures by rearranging the command sequence. That was
tried repeatedly and each apparent fix was noise.

## Known gaps in the lab manual

- The "Temperature control" section and the controller-programming appendix
  (`app:tempcontrol`) still describe the old Omega CNi8DH44 (web interface, iSeries
  config program), not the CND3. Needs the lab's front-panel procedure from the user.
- The 5209 walkthrough (BNC-cable practice run, 90° button) sits beside the newer 5302
  table; whether to rewrite it for the 5302 is the user's call.
