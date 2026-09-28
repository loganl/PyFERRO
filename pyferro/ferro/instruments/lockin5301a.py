"""EG&G / PAR Model 5301A two-phase lock-in amplifier.

*** UNVERIFIED. No manual for the 5301A exists anywhere that could be found. ***

Signal Recovery (the company that inherited the PAR/EG&G lock-in line) lists
instruction manuals for the 5302 and a dozen other models on its own support site,
but not for the 5301 or 5301A. Searches of TekWiki, xdevs and used-equipment sites
turned up nothing but other owners asking the same question. Contrast with
lockin5302.py, which is built from 221490-A-MNL-F with a page reference on every
command; there is no equivalent source for this file.

What follows is the 5302's command set (``ID``, ``SEN``, ``EX``, ``XTC``, ``FRQ``,
``XY`` -- see lockin5302.py) reused by analogy, because the 5301A is the model
immediately before the 5302 in the same product line and likely predates it by only
a little. That is a guess, not a citation, and CLAUDE.md documents at length how
expensive a wrong guess was for the 5302 itself even with hardware on the bench to
test against.

Before trusting a run recorded with this driver:
  1. Confirm ``ID`` actually answers something 5301A-shaped. If it times out or
     answers garbage, stop -- do not fall back to assuming a working link, the way
     CLAUDE.md warns against for the 5302 ("out of range means the reply is out of
     step with the command, not that the instrument has a range we do not know
     about").
  2. ``SENSITIVITIES_V`` and ``TIME_CONSTANTS_S`` (imported from lockin5302) are the
     5302's tables, not measured on a 5301A. If the two instruments' ranges differ,
     every scaled X/Y value will be silently wrong even though the raw counts and
     the SEN/XTC indices both parse as valid.
  3. Nothing here has run against a real or simulated 5301A. Every other driver in
     this package has.
"""

from __future__ import annotations

import warnings

from ..transports import Transport, TransportError
from .lockin5302 import Lockin5302


class Lockin5301A(Lockin5302):
    """Talks to a 5301A as if it were a 5302. See the module docstring before using this."""

    def __init__(self, transport: Transport) -> None:
        warnings.warn(
            "Lockin5301A speaks the 5302's command language by analogy only -- no "
            "5301A manual exists to confirm ID, SEN, EX, XTC, FRQ or XY, or the "
            "sensitivity/time-constant tables. Verify against the real instrument "
            "before trusting a run's data.",
            stacklevel=2,
        )
        super().__init__(transport)

    def check(self) -> str:
        ident = self.identify()
        if "5301" not in ident:
            raise TransportError(f"expected ID 5301(A), instrument answered {ident!r}")
        return ident
