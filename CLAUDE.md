# PyFERRO — working notes for Claude

Data acquisition for the PHY445 phase-transition lab: a lock-in amplifier measures a
sample's dielectric response while a PID controller ramps its temperature through a
transition. Replaces `FERRO v.2.vi`, a LabVIEW 2009 routine.

`pyferro/README.md` is the documentation — one file, deliberately. Don't add more doc
files; extend that one. `pyferro/CHANGELOG.md` records each release.

## Layout

| Path | What |
|---|---|
| `pyferro/ferro/` | the program — `gui/`, `acquisition.py` (measurement loop), `instruments/`, `transports.py` |
| `pyferro/tools/gpib_check.py` | GPIB diagnostics; `pixi run gpib` |
| `pyferro/tests/` | drivers, wire-level protocol tests over a pty, GUI tests, version consistency |
| `pyferro/packaging/` | `build_offline.sh`, `release.sh`, Windows launchers |
| `pyferro/docs/manuals/` | instrument manuals — **read these before guessing at instrument behaviour** |
| `ptmanual/` | LaTeX lab manual |

## Commands

Run from `pyferro/`:

```bash
pixi run simulate            # GUI against simulated instruments
pixi run start               # against real instruments
pixi run -e test test        # full suite
pixi run gpib                # diagnose the lock-in's GPIB link
```

Three test skips are expected on Windows: the pty protocol module, the chmod
writability test, and the git-tag test when HEAD is not on a tag.

## Conventions

- **Version**: one line, `__version__` in `pyferro/ferro/__init__.py`. Everything else
  derives from it and a test fails if a second copy appears. Release with
  `./packaging/release.sh [--dry-run] [--publish]`. No scripted version syncing —
  that was tried and rejected as overcomplicated.
- **Commits**: end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Pushing**: push work the user asked for without asking first; they pull it on the
  lab PC. Tagging and publishing a release stay separate decisions.
- The lab PC now has internet and a git clone at `C:\PyFERRO`. The offline bundle is
  still built and kept on a USB stick as a fallback.

## The rig, as confirmed on hardware

| | setting |
|---|---|
| EG&G 5302 lock-in | NI GPIB-USB-HS, **PAD 12**, terminator CR |
| Omega CND3 controller | RS-485 via Dtech FTDI adapter, Modbus ASCII, address 1, 9600 7E1, **read-only** |
| CND3 wiring | terminal **13 = D−**, **14 = D+**; probe on 10/11/12 |
| HP 34401A | optional second thermometer, GPIB 24, currently unplugged |

## Things learned the hard way — don't re-derive these

- The 5302 predates SCPI. Its identify command is `ID`, not `*IDN?`; NI MAX reporting
  "did not respond to a \*IDN? query" during a scan is normal and means it *was* found.
- No vendor or community driver exists for the 5302 — not NI's driver network, not
  pymeasure. `lockin5302.py` is the driver. The manual is the only authority.
- The TERMINATOR on the instrument's COMM-I/O → GPIB screen is the **input**
  terminator. The output terminator is set separately (manual §8.5), so all four
  CR/CRLF pairings occur. `probe_terminations` finds the working pair.
- SEN is 0–21 and XTC 0–18 (manual tables 9-16, 9-19). A reply outside that range means
  replies are out of step with commands, not an unknown range.
- The status byte (§8.7) names a fault after a rejected command: bit 1 invalid command,
  2 parameter error, 3 reference unlock, 4 overload, 7 data available. Reference unlock
  is expected with no sample connected.
- **Serial-polling for command complete after every exchange breaks this instrument**,
  even though §8.7 describes it. A 50 ms gap between commands is what works.
- An unpowered instrument anywhere on the GPIB chain holds NRFD/NDAC and stalls the
  bus, which also makes a scan "find" a device that never answers.
- `ibic` (MAX → Tools → NI-488.2 → Interactive Control) needs no admin rights, unlike
  MAX's board property pages. `ibln <pad> 0` asks whether anything is listening,
  which is the lowest-level question available.

## Current state of the hardware bring-up

The controller works. **The lock-in's GPIB link is marginal** — it answers, then drops
random exchanges. Ruled out: address (PAD 12 confirmed), terminator, NI driver install,
the powered-off multimeter, and the open sequence (a bisect of it failed on different
lines each run, i.e. it was measuring the flakiness itself).

Remaining suspects are physical: connector screws, cable, the adapter through a hub, or
ageing transceivers in the 5302. The transport retries twice, so the app is usable while
this is chased. `pixi run gpib` reports a success rate over twenty queries — use that to
tell whether a reseat or a cable swap actually helped, rather than a single test.

Don't respond to intermittent failures by rearranging the command sequence. That was
tried repeatedly and each apparent fix was noise.
