"""Keithley 199 System DMM/Scanner reading a Pt100 sample thermometer (optional).

A drop-in alternative to the HP 34401A (same constructor and methods). Command
reference: Model 199 Instruction Manual (docs/manuals), section 3.9.

* Predates IEEE 488.2: there is no ``*IDN?``, just as the 5302 has none. The machine
  status word identifies it instead: after ``U0X`` the next reading is a string that
  starts "199" (fig. 3-8).
* Commands are a letter plus a number, collected into one string and carried out only
  when ``X`` arrives (3.9.1). While it works through them the meter holds the GPIB
  handshake itself (bus hold-off, ``K0``, the default; table 3-12), so it needs none of
  the 5302's pauses.
* Terminator: CR LF (``Y0``, the default after a device clear; 3.9.15). Open the
  transport with ``write_termination="\\r\\n", read_termination="\\r\\n"``.
* Opening a VISA connection sends a device clear, which returns the 199 to its saved
  default conditions (3.8.5-3.8.6), whatever the front panel was showing.
  ``__init__`` therefore sets everything a reading depends on: ohms (``F2``),
  autorange (``R0``), a new reading each time it is addressed to talk (``T0``), readings
  from the A/D converter rather than the data store (``B0``), zero off (``Z0``), and
  readings with a prefix (``G0``).
* Zero (3.9.4) subtracts a stored baseline from every reading and can be saved as a
  power-up default, which the device clear brings back. A zeroed reading has the prefix
  Z (fig. 3-6); it would record the Pt100 with an offset taken off, so ``Z0`` turns
  it off, and a Z prefix is refused rather than read.
* The prefix is how an overload shows (3.9.12, and the example on page 3-3):
  ``NOHM+1.100000E+2``. The first letter is N for a normal reading and O for an
  overload, whose number is all 9s. Without the prefix (``G1``) an overload would
  parse as a large, valid-looking resistance. The next three letters name the
  function, which confirms the meter really is on ohms.
* Ohms switches between 2- and 4-terminal automatically, depending on whether the
  OHMS SENSE leads are connected (2.6.6). Connect them for a Pt100.
* There is no temperature function, so the "reading is already °C" mode of the
  34401A has no equivalent here.
"""

from __future__ import annotations

from ..transports import Transport, TransportError
from .hp34401a import pt100_to_celsius

SETUP = "F2R0T0B0Z0G0X"  # see module docstring


def parse_reading(text: str, function: str = "OHM") -> float:
    """Value from a prefixed reading such as ``NOHM+1.100000E+2``."""
    text = text.strip()
    if len(text) < 5 or text[0] not in "NO":
        raise TransportError(f"multimeter answered {text!r}, not a prefixed reading")
    if text[1:4] != function:
        raise TransportError(f"multimeter is measuring {text[1:4]}, not {function}: {text!r}")
    if text[0] == "O":
        raise TransportError(f"multimeter reading overflow ({text!r}) - out of range")
    try:
        return float(text[4:])
    except ValueError as exc:
        raise TransportError(f"multimeter answered {text!r}") from exc


class Keithley199:
    MODEL = "Keithley 199"

    def __init__(self, transport: Transport, mode: str = "pt100", r0: float = 100.0) -> None:
        if mode != "pt100":
            raise TransportError("The Keithley 199 has no temperature function; "
                                 "choose the Pt100 (ohms) reading")
        self.t = transport
        self.mode = mode
        self.r0 = r0
        self.t.write(SETUP)

    def identify(self) -> str:
        return self.t.query("U0X")

    def check(self) -> str:
        status = self.identify()
        if not status.startswith("199"):
            raise TransportError(f"expected a Model 199 status word, instrument answered {status!r}")
        return status

    def read_raw(self) -> float:
        # "X" with nothing queued changes nothing; reading the reply addresses the
        # meter to talk, which in T0 is what triggers the reading (3.9.7).
        return parse_reading(self.t.query("X"))

    def read_celsius(self) -> float:
        value = self.read_raw()
        if not 0.5 * self.r0 < value < 3.0 * self.r0:
            raise TransportError(
                f"multimeter reads {value:g} ohm; expected ~{self.r0:g}-{2 * self.r0:g} ohm. "
                "Is the Pt100 connected?")
        return pt100_to_celsius(value, self.r0)

    def close(self) -> None:
        self.t.close()
