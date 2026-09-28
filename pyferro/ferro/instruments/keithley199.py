"""Keithley 199 System DMM/Scanner.

Command reference: Model 199 Instruction Manual (docs/manuals), section 3.9,
"Programming the Model 199 Over the Bus".

* Predates IEEE 488.2 -- there is no ``*IDN?`` (section 3.9 has no such command; the
  5302 has exactly this gap too, see lockin5302.py). Identify it with the machine
  status word instead: ``U0X`` always answers with a string starting "199" (manual
  fig. 3-8).
* Device-dependent commands are one ASCII letter plus a numeric option, concatenated
  into a single string and terminated with ``X`` ("execute"); nothing happens until
  the ``X`` is sent (section 3.9, note 1: REN must also be true or the instrument
  ignores the string and shows a bus-error message).
* Default terminator is CR LF (``Y0``, section 3.9.15). Open the transport with
  ``write_termination="\\r\\n", read_termination="\\r\\n"``.
* The scanned manual's own "factory default" table is inconsistent about the
  power-on trigger mode, and this driver has no real 199 to check it against. Rather
  than trust an unclear default, ``__init__`` sets the exact state it needs
  (``F0 T0 B0 G1``: DC volts, continuous-on-talk trigger, A/D-converter readings,
  bare ASCII float with no prefix) instead of assuming power-on state -- the same
  reasoning CLAUDE.md gives for never assuming the 5302's Expand state.
* ``G1`` mode returns a bare ASCII float such as ``-1.234567E+0`` (section 3.9.12);
  ``G0`` (the alternative) prefixes it with the function mnemonic, e.g.
  ``NDCV-1234567E+0``, which is more informative for a status message but needs
  extra parsing for the numeric value alone.
* Range (``R0``..``R7``) is a table indexed by the active function (manual table 3-9);
  the scan of that table OCRs too badly to transcribe with confidence, so this driver
  only exposes the raw index and leaves range selection to the caller. ``R0`` is
  autorange on every function, and is the only range value this file is confident of.
"""

from __future__ import annotations

from ..transports import Transport, TransportError

FUNCTIONS = {
    "dcv": 0,
    "acv": 1,
    "ohms": 2,
    "dca": 3,
    "aca": 4,
    "acv_db": 5,
    "aca_db": 6,
}
AUTORANGE = 0


class Keithley199:
    def __init__(self, transport: Transport) -> None:
        self.t = transport
        # Force a known state instead of trusting power-on defaults -- see module
        # docstring. F0=DCV, T0=continuous-on-talk, B0=A/D readings, G1=bare float.
        self.t.write("F0T0B0G1X")

    # --- identification ----------------------------------------------------
    def identify(self) -> str:
        return self.t.query("U0X")

    def check(self) -> str:
        status = self.identify()
        if not status.startswith("199"):
            raise TransportError(
                f"expected a Model 199 status word (starting '199'), instrument answered {status!r}")
        return status

    # --- configuration -------------------------------------------------------
    def set_function(self, name: str) -> None:
        try:
            code = FUNCTIONS[name]
        except KeyError:
            raise ValueError(f"unknown function {name!r}; choose from {sorted(FUNCTIONS)}") from None
        self.t.write(f"F{code}X")

    def set_range(self, index: int) -> None:
        """0 = autorange, on every function. 1-7 = fixed range, meaning depends on
        the active function (manual table 3-9) -- not decoded here, see module docstring."""
        self.t.write(f"R{int(index)}X")

    def zero_enable(self, enabled: bool) -> None:
        self.t.write(f"Z{1 if enabled else 0}X")

    def filter_enable(self, enabled: bool) -> None:
        self.t.write(f"P{1 if enabled else 0}X")

    # --- data ------------------------------------------------------------
    def read_raw(self) -> float:
        # "X" with nothing queued just re-executes -- in T0 (continuous-on-talk)
        # mode, addressing the instrument to talk is what produces the next reading
        # (manual 3.9.7), which is exactly what Transport.query's write-then-read does.
        text = self.t.query("X")
        try:
            return float(text)
        except ValueError as exc:
            raise TransportError(f"multimeter answered {text!r}") from exc

    def close(self) -> None:
        self.t.close()
