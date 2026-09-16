# PyFERRO — PHY445 phase-transition lab

Data acquisition for the temperature-dependent dielectric measurement in the PHY445
advanced laboratory, plus the lab manual that describes the experiment.

| Folder | What it is |
|---|---|
| [`pyferro/`](pyferro/) | The acquisition program: reads the EG&G 5302 lock-in over GPIB and the Omega CND3 temperature controller over RS-485, plots live, and records the data. Ships as an offline Windows bundle for the lab PC. |
| [`ptmanual/`](ptmanual/) | The LaTeX source of the lab manual (`main.tex`), its figures and the standalone TikZ sources. |

![Instrument wiring](pyferro/docs/wiring-diagram.png)

## Start here

* **Everything about the program** — install, wiring, taking data, file format,
  instrument protocols, code layout, tests, packaging, releases:
  [`pyferro/README.md`](pyferro/README.md)
* **What changed between versions:** [`pyferro/CHANGELOG.md`](pyferro/CHANGELOG.md)
* **Wiring diagram and instrument manuals:** [`pyferro/docs/`](pyferro/docs/)

## Quick commands

```bash
cd pyferro
pixi run simulate           # run the GUI with simulated instruments
pixi run start              # run against the real instruments
pixi run -e test test       # run the test suite
./packaging/build_offline.sh  # build the offline Windows bundle
```

```bash
cd ptmanual
make                        # build the TikZ figures and main.pdf
```

## Background

The program replaces `FERRO v.2.vi`, a LabVIEW 2009 routine that recorded temperature
with the lock-in X and Y outputs. The experiment now uses a self-contained Omega CND3
PID controller, which reports the sample temperature to the computer over RS-485, so
the acquisition software only reads instruments and never drives the heater.

## Versioning

`MAJOR.MINOR.PATCH`, tagged `v<version>` in git. The same number appears in the window
title and in the header of every data file, so a recorded measurement can be traced to
the exact code that produced it. See
[`pyferro/docs/releasing.md`](pyferro/docs/releasing.md).
