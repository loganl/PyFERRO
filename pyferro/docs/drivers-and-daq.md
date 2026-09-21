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
| GUI | `gui/main_window.py` | Draws plots and readouts. Never talks to an instrument. |
| **DAQ** | `acquisition.py` | When to read, what to do when a read fails, what to save. |
| **Drivers** | `instruments/lockin5302.py`, `cnd3.py`, `hp34401a.py` | What the instrument's answers mean. |
| **Transports** | `transports.py`, minimalmodbus | How bytes get there and back. |
| OS / vendor | NI-VISA → NI-488.2; pyserial → FTDI | Hardware access, from outside this repo. |
| Wires | GPIB → 5302; RS-485 → CND3 terminals 13/14 | Electrical signals. |

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

Three classes implement it: `VisaTransport`, `SerialTransport`, and `SimLockinTransport`
in the simulator.

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
signed 16-bit and divides by ten: `FFF6` → 65526 − 65536 = −10 → **−1.0 °C**.

**Who does what.** minimalmodbus builds the frame, computes the LRC (or CRC-16 for RTU,
the same content in raw binary), sends it through pyserial, and validates the reply's
checksum, address and function. `CND3` just asks for `read_registers(0x1000, 2)` and
interprets the answer. Its `_read` retries twice, sleeping 50 ms then 100 ms, and it
**never writes** — so it cannot touch the setpoint or the heater. `autodetect` works
through mode × baud × framing, asking for the firmware register until something answers.

On the wire, 17 characters out and 19 back is about **37 ms**, plus the controller's own
response time.

### Multimeter: `HP34401A`

Source: [`ferro/instruments/hp34401a.py`](../ferro/instruments/hp34401a.py)

`READ?` returns ohms. The driver solves the Callendar–Van Dusen quadratic
R = R₀(1 + A·T + B·T²) for T: 109.73 Ω → 25.0 °C, 157.33 Ω → 150.0 °C. A sanity window of
0.5–3 × R₀ catches a meter left in the wrong mode.

### Simulation

Source: [`ferro/instruments/simulated.py`](../ferro/instruments/simulated.py)

The fakes sit **below** the drivers. `SimLockinTransport` implements the transport
contract and answers `SEN` and `XY` as a 5302 would, so the real parsing and scaling code
runs. `SimModbusInstrument` stands in for `minimalmodbus.Instrument`, so the real
`decode_temperature` runs. One shared `SimulatedSample` keeps all three instruments
agreeing on the temperature.

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

`open_lockin` branches three ways: simulated, serial, or GPIB. The GPIB path runs the
terminator probe with a short timeout and **no retries**, so a wrong pair fails fast;
caches the winning pair per resource; then restores the full timeout and turns retries
on. `open_pid` and `open_dmm` are simpler.

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

### `_read`: the wrapper around every instrument call

- It times the call — the source of the "falling behind" message's numbers.
- On the **first** failure it logs one warning. It is edge-triggered: one message when
  something breaks, not one per sample.
- When a device recovers it logs "answering again after N s".
- On failure it returns `None`, and the caller records NaN plus a flag.

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

The bits add, so `flags = 6` means the lock-in and the controller both failed that
sample. `T_C` then comes from whichever temperature source is selected, the ramp
direction updates, and the over-temperature alarm fires once, as the limit is crossed.

### Ramp direction: `DirectionTracker`

Source: [`ferro/analysis.py`](../ferro/analysis.py)

A least-squares slope over the last 60 s — at least 3 points spanning at least 10 s:

```
slope = Σ(t − t̄)(T − T̄) / Σ(t − t̄)²
```

| Slope | Direction |
|---|---|
| above +0.2 °C/min | heating |
| below −0.2 °C/min | cooling |
| magnitude under 0.1 °C/min | steady |
| in between | unchanged — hysteresis, so noise at a turning point doesn't flicker |

`segment` counts the flips between heating and cooling.

### Watching settings

`_watch_lockin` runs every sample and costs nothing, since SEN, EX and overload arrive
with each reading. `_poll_settings` runs every 60 s for XTC, FRQ and the controller's
mode and run state, which each need an extra exchange. Any change is **annotated** —
logged, and written into the data file as `# HH:MM:SS …` — so the file explains itself.

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
