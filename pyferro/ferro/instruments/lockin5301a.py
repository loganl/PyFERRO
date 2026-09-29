"""EG&G / PAR Model 5301A two-phase lock-in amplifier.

*** UNVERIFIED: no 5301A manual could be found, so this is built by analogy. ***

Signal Recovery, which inherited the PAR/EG&G lock-in line, publishes manuals for the
5302 and a dozen other models but none for the 5301 or 5301A, and searches of TekWiki,
xdevs and used-equipment sites found only other owners looking for one. lockin5302.py
cites the 5302 manual for every command; nothing here can be cited.

The 5301A is taken to speak the 5302's language because it is the model just before
it in the same line: ``ID``, ``SEN``, ``EX``, ``XTC``, ``FRQ``, ``XY``, the same
sensitivity and time-constant tables, the same +/-10000-count full scale. It is also
opened the same way (``acquisition.open_lockin``): the terminator search, 50 ms
between commands, and 50 ms between writing a query and reading its reply. That delay
is what finally made the 5302 work (CLAUDE.md, 2026-09-24); without it replies land
on the next query. A 5301A that is 5302-like will very likely need it too.

Before trusting data recorded with this driver:

1. ``ID`` must answer with something containing "5301". If it times out or answers
   garbage, stop and work from the front panel and a serial poll instead - the
   process CLAUDE.md records for bringing up the 5302.
2. Compare the SEN and XTC settings the program reports (Test button, file header)
   with the front panel. If they disagree, the tables are wrong for this model and
   every scaled X and Y is wrong with them, even though everything parses.
3. Compare a recorded X/Y against the front-panel meters at a known signal.

None of this has been run against a real 5301A; only against the 5302 simulation
answering ``ID`` as a 5301A would.
"""

from __future__ import annotations

import warnings

from ..transports import Transport, TransportError
from .lockin5302 import SENSITIVITIES_V, TIME_CONSTANTS_S, Lockin5302, parse_ints

UNVERIFIED = ("The 5301A driver is unverified: no manual exists to confirm its commands "
              "or ranges, so it assumes the 5302's. Check ID, the sensitivity and the "
              "time constant against the front panel before trusting the data.")


def _in_table(value: int, size: int, command: str) -> int:
    # Unlike on the 5302, an out-of-range value does not prove the replies are out of
    # step: the 5301A's table may simply be longer than the 5302's.
    if not 0 <= value < size:
        raise TransportError(
            f"{command} answered {value}, outside the 5302's 0..{size - 1}. Either the "
            "replies are out of step with the commands, or the 5301A's table differs "
            "from the 5302's - there is no manual to say which.")
    return value


class Lockin5301A(Lockin5302):
    """A 5302 driver that expects ID 5301. Read the module docstring first."""

    MODEL = "5301A"

    def __init__(self, transport: Transport) -> None:
        warnings.warn(UNVERIFIED, stacklevel=2)
        super().__init__(transport)

    def check(self) -> str:
        ident = self.identify()
        if "5301" not in ident:
            raise TransportError(f"expected ID 5301, instrument answered {ident!r}")
        return ident

    def sensitivity_index(self) -> int:
        return _in_table(parse_ints(self.t.query("SEN"), 1)[0], len(SENSITIVITIES_V), "SEN")

    def time_constant_index(self) -> int:
        return _in_table(parse_ints(self.t.query("XTC"), 1)[0], len(TIME_CONSTANTS_S), "XTC")
