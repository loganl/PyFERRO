# Drivers and DAQ

How PyFERRO turns bytes on a GPIB cable and an RS-485 pair into a row in a data file —
from the wires up to the measurement loop, with every example taken from this rig.

1. [The layers](#1-the-layers)
2. [The wires](#2-the-wires-just-enough-to-read-the-code)
3. [Transports](#3-transports)
4. [Drivers](#4-drivers)
5. [DAQ](#5-daq)
6. [One sample, traced](#6-one-sample-traced)
7. [Check yourself](#7-check-yourself)

## 1. The layers

| Layer | Files | Job |
|---|---|---|
| GUI | `gui/main_window.py`, `setup_panel.py`, `lockin_panel.py` | Draws plots and readouts. Never talks to an instrument the loop has open. |
| **DAQ** | `acquisition.py` | When to read, what to do when a read fails, what to save. |
| **Drivers** | `instruments/sr830.py`, `lockin5302.py`, `cnd3.py`, `hp34401a.py` (+ other models, §4) | What the instrument's answers mean. |
| **Transports** | `transports.py`, minimalmodbus | How bytes get there and back. |
| OS / vendor | NI-VISA → NI-488.2; pyserial → FTDI | Hardware access, from outside this repo. |
| Wires | GPIB → SR830 (or 5302); RS-485 → CND3 terminals 13/14 | Electrical signals. |

Each layer talks only to the one directly below it. `Lockin5302` never mentions GPIB;
`Acquisition` never mentions `SEN`. That separation is why simulation works: replace the
bottom of the stack and everything above runs unchanged. This document covers the three
layers in bold.

## 2. The wires, just enough to read the code

### GPIB (IEEE-488)

A parallel bus: eight data lines plus control lines. One **controller** — the NI
GPIB-USB-HS, at address 0 — shares it with up to 30 devices, each with a **primary
address**. The 5302 is PAD 12. Before data moves, the controller *addresses* the bus:
"12, listen" or "12, talk".

Every byte crosses with a three-wire handshake:

- **NRFD**, not ready for data — a listener holds it until it can take a byte
- **DAV**, data valid — the talker asserts it once the byte is on the lines
- **NDAC**, not data accepted — a listener holds it until it has the byte

So the bus runs at the speed of its slowest listener. Two failures seen on this rig
follow directly: an **unpowered instrument** holds these lines down and nothing moves;
and **"no listeners" (ENOL)** means NRFD and NDAC were both released when the controller
started — nobody was there to hold them.

A message ends with the **EOI** line on its last byte, and/or a **terminator byte**: CR
(`0x0D`) or LF (`0x0A`). Two more bus operations appear in the code. A **serial poll**
reads one status byte from a device, answered by its interface chip rather than its
command processor. **Device Clear** resets a device's interface and buffers.

### RS-485

The signal is the voltage *difference* between two wires, D+ and D−, which makes it
resistant to noise. It is half-duplex — one side talks at a time — and multi-drop, so
the protocol carries a device address; the CND3 is Modbus address 1. The Dtech adapter
appears to Windows as an ordinary COM port.

Characters travel as asynchronous serial. At **9600 7E1** each is 1 start + 7 data + 1
parity + 1 stop bit = 10 bits, about **1.04 ms per character**.

### VISA

A vendor-neutral API: open a *resource* by name (`GPIB0::12::INSTR`), then write, read,
set a timeout, set terminators, read the status byte, clear. NI-VISA implements it on
NI-488.2; **pyvisa** wraps it for Python. The `@py` backend, pyvisa-py, is pure Python
and **cannot** drive NI GPIB hardware.

## 3. Transports

Source: [`ferro/transports.py`](../ferro/transports.py)

Every driver needs exactly three calls, and that is the whole contract:

```python
write(cmd)          # send; no reply expected
query(cmd) -> str   # send; return the reply text
close()
```

`VisaTransport` and `SerialTransport` implement it for real hardware, and the simulator
has one fake per instrument model (`SimLockinTransport` for the 5302, `SimSR830Transport`,
`SimDMMTransport`, `SimK199Transport`).

### Terminators, as actual bytes

This is what `ibic` showed on the rig:

| Direction | Bytes | Meaning |
|---|---|---|
| PC → 5302 | `49 44 0D` | "I" "D" CR — write termination CR |
| 5302 → PC | `35 33 30 32 0D` | "5" "3" "0" "2" CR — read termination CR |

pyvisa appends `write_termination` to everything it sends, and stops reading at
`read_termination`. If that byte never comes, the read runs to the timeout and fails
with `VI_ERROR_TMO` — indistinguishable from a dead instrument. `None` means stop only
on EOI.

`probe_terminations` tries each pair in `TERMINATIONS` in order. It is generic: the
caller passes in `open_one(write, read)` and `verify(transport)`, so it knows nothing
about the 5302. It keeps the first pair that verifies, closes every rejected transport,
and tags the winner with `.terminators` so the caller can try that pair first next time.

### `VisaTransport`, method by method

- **`__init__`** tries NI-VISA, then `@py`; opens the resource; sets the timeout (in ms)
  and both terminators; sends **Device Clear**, which also empties replies a previous
  program never collected.
- **`_lock`** exists because two threads can reach one instrument: the acquisition loop
  and a **Test** button's worker. Without it their bytes interleave on the bus and each
  reads the other's reply.
- **`query`** loops `retries + 1` times. After a failure it serial-polls and decodes the
  status byte, drains stale replies, then tries again. When the retries run out it
  raises `TransportError` with the decoded reason attached, and `retries_used` counts
  how often it had to.
- **`_pause`** is the 50 ms gap: the 5302 loses a command that arrives while it is still
  busy with the last one.
- **`reply_delay_s`** is a second 50 ms wait, between sending a query and reading its
  reply (`pyvisa`'s `query(delay=...)`). The instrument must parse the command before it
  can talk; addressed too soon, its reply is late and the *next* query reads it. On the
  rig this was the difference between the Test button failing 10 times in 10 and
  succeeding 15 in 15. The multimeter leaves it at 0.
- **`_recover` / `drain_replies`** handle a subtle trap. A query that *times out* can
  still have its reply arrive afterwards, and then the *next* query reads that late
  reply instead of its own — which is how `5302` once turned up as a sensitivity index.
  Draining with a 200 ms timeout discards anything in flight.
- **`read`** fetches one more line, for when `XY`'s two values arrive separately.

### `SerialTransport` — the same contract over RS-232

Over RS-232 the 5302 echoes each character, sends its reply ending in CR LF, and then
sends a **prompt**: `*` if all is well, `?` on an error. `_exchange` clears the input
buffer, writes, reads 64-byte chunks until the tail ends in a prompt, and strips the
echo. It accepts a prompt in only three positions: as the whole message, after a CR/LF,
or right after the echoed command (a command with no reply).

> The prompt marks the end of every exchange, so serial replies **cannot** get out of
> step. GPIB has no such marker — which is why the VISA side needs draining and range
> checks and the serial side does not.

## 4. Drivers

### Lock-in: `Lockin5302`

Source: [`ferro/instruments/lockin5302.py`](../ferro/instruments/lockin5302.py)

| Command | Returns |
|---|---|
| `ID` | `5302` |
| `SEN` | sensitivity index 0–21: 100 nV to 1 V in a 1-2-5 sequence |
| `EX` | 1 when Expand X is on |
| `XTC` | time-constant index 0–18 |
| `FRQ` | reference frequency in **mHz** |
| `XY` | X and Y as integer **counts** |
| `IE`, `DR`, `FLT`, `PREAMP` | codes for reference mode, dynamic reserve, filter, signal input |
| `OA`, `OF` | oscillator level and frequency as `n1 n2`: a count and its range |
| `P` | reference phase as `quadrant millidegrees` |

The last three rows are read by `setup()`, for the **Test** button, the data-file header,
the Lock-in tab and the once-a-minute poll during a run (§5) — a record of how the front
panel was set, following the lab manual's 5302 table. Each is read on its own and becomes
`not read` if it fails, so a dropped exchange there cannot fail a Test or a recording.
AC/DC coupling and FLOAT/GND are latching keys with no GPIB command and cannot be read
at all.

**Setting the 5302.** Every command above that reads a value also sets it when given a
number: `SEN 21`, `XTC 8`, `EX 0`, `IE 0`, `OF 2500 7`, `OA 5000 1`, `DR 1`, `FLT 0`,
`PREAMP 1`. `SETTABLE` lists what the driver can set, in the order `apply()` sends it
— the time constant before the reserve, because a FAST time constant forces MIN reserve
(manual §4.3). `commands_for` builds every command, checking each value, before
`apply()` sends the first, so a bad value sends nothing. `oscillator_frequency_command`
and `oscillator_level_command` do the count-and-range encoding: 25 kHz is `OF 2500 7`
(2500 in the 10–100 kHz decade), 0.5 V is `OA 5000 1` (5000 steps of 0.1 mV).

**Checking against the lab manual.** `CAPACITANCE_SETUP` is the lab manual's 5302 table
as data — setting, wanted value, and which front-panel key changes it — and
`check_setup(settings)` compares a `settings()` dict with it row by row: ✔, ✘, or "not
read". The Test button shows the result as a table, a recording writes it into the
header (`# lockin_setup_check:`), and the Lock-in tab's *Lab-manual values* fills its
form from the same table. Keep it in step with `ptmanual/main.tex`.

The instrument speaks **counts, not volts**. ±10000 counts is full scale; readings run to
±12000 before clipping.

```
V = counts / 10000 × full_scale[SEN]
X only: ÷ 10 again when EX = 1
```

**Expand multiplies the gain of the x channel only** — manual §4, "causes the x channel
output to be multiplied by 10". Y keeps its full scale.

Worked example: `SEN` answers 17 (50 mV full scale) and `XY` answers `5000,-2500`. Then
X = 5000/10000 × 50 mV = **25 mV** and Y = **−12.5 mV**. With Expand on, X becomes
**2.5 mV** and Y stays **−12.5 mV**.

**Why `read()` asks for `SEN` and `EX` every sample:** if a student presses SEN↑
mid-run, the same counts now mean different volts. The LabVIEW routine scaled with a
value stored once at the start, which is how the 2025 data came out pinned at
1.00×10⁻⁷ V. Two short exchanges per sample buy scaling that cannot go stale. If either
fails, the sample is lost rather than guessed.

- **`XY`** may arrive on one line or two. The regex collects every integer; if it found
  only one, the driver reads another line.
- **`parse_ints`** matches `[-+]?\d+`, so it ignores whatever delimiter separates the
  values — the `DD` command can set any character.
- **`checked_index`** treats an index outside 0–21 or 0–18 as "replies out of step", not
  as a range we don't know about. The manual's tables 9-16 and 9-19 match ours.
- **`LockinReading`** carries the result, with derived `r_v` = √(X²+Y²), `theta_deg`,
  `overloaded` (≥ 12000 counts) and `percent_fs`.

### Controller: `CND3` and minimalmodbus

Source: [`ferro/instruments/cnd3.py`](../ferro/instruments/cnd3.py)

**Modbus in brief.** The device is a table of 16-bit **registers**. Function **03**
reads a run of them. A request names the device address, the function, a start register
and a count; the reply gives a byte count, then the values; a checksum closes every
frame. Here is the request PyFERRO sends every sample:

```
:  01  03  1000  0002  EA  CR LF
│  │   │   │     │     └─ LRC checksum
│  │   │   │     └─ read 2 registers: PV and SV
│  │   │   └─ starting at register 1000H (PV)
│  │   └─ function 03, read
│  └─ Modbus address 1
└─ start of frame
```

**LRC:** add the bytes, `01+03+10+00+00+02 = 0x16`, then take the two's complement,
`(−0x16) & 0xFF = 0xEA`.

The reply is `:01 03 04 05DC 0640 D1`: four data bytes, where `05DC` = 1500 →
**150.0 °C** process value and `0640` = 1600 → **160.0 °C** setpoint.

`decode_temperature` checks the fault codes 8002H–8007H first, then reads the word as
signed 16-bit and divides by ten: `FFF6` → 65526 − 65536 = −10 → **−1.0 °C**. Anything
below −999.9 °C is refused as a status code: no input goes that low, and an unlisted
code such as `8000` would otherwise become **−3276.8 °C** — the "−3000 °C" spikes once
seen on the first readings of a run.

**Who does what.** minimalmodbus builds the frame, computes the LRC (or CRC-16 for RTU,
the same content in raw binary), sends it through pyserial, and validates the reply's
checksum, address and function. `CND3` just asks for `read_registers(0x1000, 2)` and
interprets the answer. Its `_read` retries twice, sleeping 50 ms then 100 ms, and it
**never writes** — so it cannot touch the setpoint or the heater. `autodetect` works
through mode × baud × framing, asking for the firmware register until something answers.

Beyond PV and SV, `status()` gathers what **Test controller** shows and the data-file
header records: firmware, output 1 level, control method (1005H), run state (103CH) and
°C/°F, plus `setup()` — output 2, output 1's upper limit, the setpoint mode and its ramp
rate. Like the 5302's `setup()`, each of those is read on its own and becomes `None` if
it fails, because they explain a run rather than measure it.

On the wire, 17 characters out and 19 back is about **37 ms**, plus the controller's own
response time.

### Multimeter: `HP34401A`

Source: [`ferro/instruments/hp34401a.py`](../ferro/instruments/hp34401a.py)

`READ?` returns whatever the meter is measuring: the driver sends no function command,
so the meter must be put in 4-wire ohms on its front panel. The driver solves the
Callendar–Van Dusen quadratic R = R₀(1 + A·T + B·T²) for T: 109.73 Ω → 25.0 °C,
157.33 Ω → 150.0 °C. A sanity window of 0.5–3 × R₀ catches a meter left in the wrong
mode — on the rig it was once found on DC volts.

### Other models: one shape, several drivers

The lab's lock-in is now the SRS SR830, the default; the 5302 above, its predecessor, is
still supported, and the walkthrough uses it because its quirks are the instructive
ones. The multimeter can be a 34401A or a Keithley 199. `open_lockin` and `open_dmm`
look the driver up in `LOCKIN_MODELS` / `DMM_MODELS`, and nothing above them knows which
one they got, because every lock-in driver has the same methods (`check`, `read`,
`settings`, `apply`, `close`) and attributes (`MODEL`, `SENSITIVITY_LABELS`,
`TIME_CONSTANT_LABELS`, `EXPAND_NAME`), and every reading the same fields (`x_v`, `y_v`,
`r_v`, `theta_deg`, `sensitivity`, `full_scale_v`, `percent_fs`, `expand`,
`overloaded`). The multimeters share `check`, `read_raw`, `read_celsius`, `close` and
`MODEL`.

Each lock-in driver also describes its own settings, so the GUI and the loop need no
per-model code: `SETUP_TABLE` (its lab-manual table, checked by the shared
`check_setup`), `SETUP_NAME`, `SETUP_NOTE`, `PANEL` (the Lock-in tab's controls:
key, label, and a kind — a choice, a position in a list, kHz, volts or a check box),
`PANEL_NOTE` (what only the front panel can do), and `commands(changes)`, which builds
and checks every command before anything is sent. The Lock-in tab rebuilds itself from
`PANEL` when the model changes.

What differs is what the manuals say, and each difference shows up somewhere specific:

- **SR830** ([`sr830.py`](../ferro/instruments/sr830.py)) is IEEE 488.2 and does its own
  flow control, so it gets fixed LF terminators and none of the 5302's delays. It answers
  in volts, so there is no scaling. Its overload status byte **latches and clears on
  read**. The driver reads it exactly once per sample, after the data. Two reads (say one
  for overload, one for unlock) would each clear what the other was looking for. For the
  same reason a retried read may have lost the bits, so a sample whose status read
  needed a retry is flagged as overloaded. Every front-panel setting has a command, so
  unlike the 5302 nothing is unreadable: `setup()` reads `FMOD?`, `SLVL?`, `ISRC?`,
  `ICPL?`, `IGND?`, `ILIN?`, `RMOD?`, `OFSL?`, `DDEF?` and `PHAS?`. `SETTABLE` sends the
  reference first (`FREQ` is refused unless it is internal) and the reserve and slope
  before the time constant, which the instrument raises to the shortest they allow.
  Several commands can share a line, separated by `;`: that is how one entry turns
  every offset and expand off.
- **Keithley 199** ([`keithley199.py`](../ferro/instruments/keithley199.py)) has no
  `*IDN?`, like the 5302. The device clear that `VisaTransport` sends on opening resets
  it, so the driver sets its own function, and turns Zero off, every time. Its readings carry a prefix, and
  that prefix is the only place an overload shows: without it, overflow is a
  plausible-looking 9.999999E+9 Ω.

### Simulation

Source: [`ferro/instruments/simulated.py`](../ferro/instruments/simulated.py)

The fakes sit **below** the drivers. `SimLockinTransport` implements the transport
contract and answers `SEN`, `XY` and the rest as a 5302 would — starting in the lab
manual's set-up, and taking new values from `SEN 21`-style commands — so the real
parsing, scaling and setting code runs. `SimSR830Transport` does the same with the
SR830's commands, in volts. `SimModbusInstrument` stands in for `minimalmodbus.Instrument`, so the real
`decode_temperature` runs. One shared `SimulatedSample` keeps all three instruments
agreeing on the temperature. `lockin_transport` and `dmm_transport` pick the fake that
matches the selected model.

## 5. DAQ

Source: [`ferro/acquisition.py`](../ferro/acquisition.py)

### Threads

| Thread | Job | May block? |
|---|---|---|
| GUI (Qt) | draw, respond to clicks | **Never** — the window would freeze |
| Acquisition | all instrument I/O | Yes, for seconds |

They communicate three ways. **Callbacks** (`on_sample`, `on_log`, `on_status`,
`on_recording`), which the GUI wraps in Qt signals so they queue safely across threads.
**Stop**, a `threading.Event`. And **Record on/off**, a request variable under
`_req_lock` that the loop picks up at the top of its next pass — only the acquisition
thread ever opens or closes the data file.

### Opening instruments

`open_lockin` picks the driver for the selected model, then branches: simulated, SR830
(GPIB with LF terminators, nothing to probe), serial, or GPIB. The GPIB path, used by
the 5302, runs the terminator probe with a short timeout and **no
retries**, so a wrong pair fails fast; caches the winning pair per resource; then
restores the full timeout and turns retries on. `open_pid` and `open_dmm` are simpler;
`open_dmm` gives the Keithley 199 its CR LF terminators.

### `InstrumentSlot`: one state machine per instrument

```
 off ──get() opens it──► ok ◄── a read succeeds
  ▲                      │
  │ 3 failures: close,   │ a read fails
  │ reopen after 5 s     ▼
  └────────────────── error
```

`get()` opens the device lazily, and while the 5-second backoff runs it raises "waiting
to reconnect" instead. `failed()` counts failures, and on the third closes the device and
schedules a reopen.

The two thermometer slots, controller and multimeter, also have `discard_after_open`
(`DISCARD_TEMPERATURES_AFTER_OPEN`, 3). Every successful open, at the start of a run or
on a reconnect, resets `to_discard` to it, and that many readings are thrown away before
any is trusted.

### `_read`: the wrapper around every instrument call

- It times the call — the source of the "falling behind" message's numbers.
- On the **first** failure it logs one warning. It is edge-triggered: one message when
  something breaks, not one per sample.
- When a device recovers it logs "answering again after N s".
- On failure it returns `None`, and the caller records NaN plus a flag.
- While a slot still has readings to discard it returns `DISCARDED`: the instrument
  answered (its light stays green), but the caller records NaN with flag 16, not the
  error flag.

### `_run`: the clock

Readings follow an **absolute schedule**, `next_tick = t0 + n × interval`, so sleep errors
never accumulate into drift. With a 0.2 s interval and a 0.15 s reading, the loop sleeps
0.05 s. If a reading takes 2.1 s, `delay` comes out negative, so the loop resets the
schedule to *now* rather than firing a burst of catch-up reads, and warns at most every
30 s.

It waits with `_stop.wait(delay)` rather than `sleep`, so Stop wakes it instantly. The
body sits inside `try/except` so one bad sample can't kill the thread, and `finally`
closes the file and instruments however the loop ends.

### `_sample`: building one row

It reads the controller, then the multimeter if enabled, then the lock-in, into a dict
whose keys are the file's columns. A failure becomes NaN plus a **flag bit**:

| Bit | Value | Meaning |
|---|---|---|
| 0 | 1 | lock-in overload |
| 1 | 2 | lock-in error |
| 2 | 4 | controller error |
| 3 | 8 | multimeter error |
| 4 | 16 | temperature discarded just after connecting |

The bits add, so `flags = 6` means the lock-in and the controller both failed that
sample. `T_C` then comes from whichever temperature source is selected, the ramp
direction updates, and the over-temperature alarm fires once, as the limit is crossed.

### Ramp direction: `DirectionTracker`

Source: [`ferro/analysis.py`](../ferro/analysis.py)

The rig's temperature readings wobble by degrees around the ramp, whether from the
heater cycling or a noisy probe. A local slope follows every swing — the first version of this tracker used one and flipped
between heating and cooling on each. So the tracker does two things instead:

1. **Average** the temperature over the last 120 s. Swings much faster cancel out.
2. **Turn only at a turning point.** While heating it remembers the highest average
   reached, and calls the ramp cooling once the average is **3 °C** below that peak;
   the mirror image while cooling. A wobble smaller than the band never turns it,
   however steep.

| State | Becomes | When |
|---|---|---|
| 0, not yet known | heating / cooling | the average has moved 3 °C from where it started |
| heating | cooling | the average is 3 °C below the highest it reached |
| cooling | heating | the average is 3 °C above the lowest it reached |

A turn is recognised a minute or two after the real extreme. At that moment
`just_turned` is set and `turn_time_s` / `turn_temp_c` give the extreme, so `_sample`
annotates the file (`# Ramp turned to cooling at 150.1 °C, t = 7512 s …`) and the
window recolours the points since then. The rows already written keep the old label.

On synthetic 25 → 150 → 25 °C runs at 1–10 °C/min with ±5 °C swings of period 5–120 s it
turned exactly once every time, within 4 s of the true peak; the old slope tracker
turned hundreds of times once the swings were 30 s or slower. `segment` counts the
turns; `slope_c_per_min`, for the display only, is a least-squares slope of the averaged
temperature over the last 120 s.

### Watching settings

`_watch_lockin` runs every sample and costs nothing, since SEN, EX and overload arrive
with each reading. `_poll_settings` runs every 60 s: the lock-in's full `settings()`
(the reference, oscillator, filters, reserve, input and phase, and on the SR830 also
coupling, grounding, slope and displays — `LOCKIN_WATCHED` lists what is compared) and the controller's mode and run
state, which each need extra exchanges. Any change is **annotated** — logged, and
written into the data file as `# HH:MM:SS …` — so the file explains itself. Each poll
also goes to `on_lockin`, which fills the window's Lock-in tab.

### Changing lock-in settings during a run

The window never talks to an instrument the acquisition thread has open: two threads
on one GPIB device would interleave their bytes. So the Lock-in tab calls
`set_lockin(changes)`, which only stores the request under `_req_lock`, like
`set_recording`. At the top of its next pass the loop's `_handle_lockin_request` sends
it with the driver's `apply()`, annotates what was sent (`Lock-in set from PyFERRO:
filter FLAT (FLT 0)`), and brings the next poll forward, so the read-back — and any
"changed to" line — follows at once. The file therefore shows both what was asked for
and what took effect. `apply()` checks every value before sending the first command,
and sends the time constant before the reserve, since a FAST time constant forces MIN
reserve. When no run is active the tab opens its own connection instead, like Test.

### Recording

Source: [`ferro/datafile.py`](../ferro/datafile.py)

`_handle_record_request` picks up the Record button. `_open_writer` creates a unique path
and writes a header including the instrument settings read at that moment. `_maybe_write`
applies the ΔT gate. `DataWriter.write` writes a tab-separated row and **flushes and
fsyncs every row**, so a power cut loses at most one. `_close_writer` closes the file and
copies the session log beside it.

## 6. One sample, traced

A 0.2 s interval with both instruments healthy:

| t (s) | Layer | What happens | On the wire |
|---|---|---|---|
| 0.000 | DAQ | `_run` tick → `_read("pid")` | |
| | Transport | minimalmodbus → pyserial, ~18 ms out | `:010310000002EA␍␊` |
| | Transport | reply, ~20 ms + controller | `:01030405DC0640D1␍␊` |
| | Driver | `[1500, 1600]` → PV 150.0 °C, SV 160.0 °C | |
| ≈ 0.05 | DAQ | `_read("lockin")` → `Lockin5302.read()` | |
| | Transport | sensitivity, then 50 ms gap | `SEN␍` → `17␍` |
| | Transport | expand, then 50 ms gap | `EX␍` → `0␍` |
| | Transport | data, then 50 ms gap | `XY␍` → `5000,-2500␍` |
| | Driver | X 25 mV, Y −12.5 mV at 50 mV full scale | |
| ≈ 0.22 | DAQ | row built, direction updated, `on_sample` → Qt queue → plots; row written and fsynced | |
| | DAQ | `next_tick` was 0.2 and has passed → no sleep this time | |

> The lock-in's three 50 ms gaps alone take 150 ms. **A 0.2 s interval is just below
> what this rig can sustain** — 0.25 to 0.3 s is the realistic floor.

## 7. Check yourself

<details><summary>The lock-in returns <code>12000,300</code> with SEN = 15, EX = 0. What is X, and which flag is set?</summary>

12000/10000 × 10 mV = **12 mV**. 12000 counts is the overload threshold, so flag value
**1**.
</details>

<details><summary>SEN = 17, EX = 1, and <code>XY</code> answers <code>4000,4000</code>. What are X and Y?</summary>

Full scale is 50 mV. X = 4000/10000 × 50 mV ÷ 10 = **2 mV**. Expand is X only, so
Y = 4000/10000 × 50 mV = **20 mV**.
</details>

<details><summary>Why does <code>SerialTransport</code> never need <code>drain_replies</code>?</summary>

Every serial exchange ends at a prompt, `*` or `?`, so a reply can't spill into the next
exchange.
</details>

<details><summary>The CND3's PV word is <code>FF38</code>. What temperature is that?</summary>

65336 − 65536 = −200 → **−20.0 °C**.
</details>

<details><summary>At a 1 s interval, the RS-485 wires come loose for 20 s. What appears in the log and the file?</summary>

One warning when it breaks. Rows get PV = NaN with flag **4**. On the third failure the
slot closes and retries roughly every 5 s, logging each attempt. When the wires
reconnect: "answering again after ~20 s".
</details>

<details><summary>The loop is sleeping between samples. Why does Stop still respond immediately?</summary>

It waits with `_stop.wait(delay)`, which returns the moment the Stop event is set.
</details>

<details><summary>What goes wrong if the GUI thread calls <code>Lockin5302.read()</code> directly?</summary>

The window freezes for every instrument delay — several seconds on a timeout.
</details>
