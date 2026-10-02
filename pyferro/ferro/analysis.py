"""Small numeric helpers used by the acquisition loop."""

from __future__ import annotations

from collections import deque

NAN = float("nan")


class DirectionTracker:
    """Split a run into heating (+1) and cooling (-1) branches.

    The rig's temperature readings are not smooth: they swing by degrees around the
    ramp (40.5 -> 44.5 -> 40.6 C within 3 s on 2026-09-29), whether from the heater
    cycling or a noisy probe. A local slope follows every swing, and the earlier
    slope-and-threshold tracker flipped between heating and cooling on each one. Two
    steps make this robust:

    1. **Average** the temperature over the last ``smooth_s`` seconds. Swings much
       faster than that cancel out.
    2. **Turn only at a turning point.** While heating, remember the highest averaged
       temperature reached; the ramp has turned to cooling once the average is
       ``band_c`` below it, and the mirror image while cooling. A wobble smaller than
       the band never turns it, however steep it is.

    A turn is recognised some time after the true extreme (the band, plus about half
    of ``smooth_s``). On the update that recognises it ``just_turned`` is True, and
    ``turn_time_s`` / ``turn_temp_c`` say where the extreme was, so the plot can
    recolour the points in between and the data file can record it.

    ``direction`` is 0 until the temperature has first moved ``band_c`` from where it
    started. ``segment`` increments at each turn between heating and cooling.
    ``slope_c_per_min`` (for display) is a least-squares slope of the averaged
    temperature over the last ``slope_window_s``.
    """

    # Tested on synthetic 25 -> 150 -> 25 C runs at 1-10 C/min with +-5 C swings of
    # period 5-120 s: exactly one turn every time, recognised turn within 4 s of the
    # true peak. A 60 s average let 120 s swings through (+-3 C left > band).
    def __init__(self, smooth_s: float = 120.0, band_c: float = 3.0,
                 slope_window_s: float = 120.0) -> None:
        self.smooth_s = smooth_s
        self.band_c = band_c
        self.slope_window_s = slope_window_s
        self._raw: deque[tuple[float, float]] = deque()
        self._avg: deque[tuple[float, float]] = deque()  # (window centre, average)
        self._hi: tuple[float, float] | None = None  # highest average since the last turn
        self._lo: tuple[float, float] | None = None  # lowest
        self.direction = 0
        self.segment = 0
        self.slope_c_per_min = NAN
        self.just_turned = False
        self.turn_time_s = NAN
        self.turn_temp_c = NAN

    def update(self, t_s: float, temp_c: float) -> int:
        self.just_turned = False
        if temp_c != temp_c:  # NaN: keep the previous state
            return self.direction
        raw = self._raw
        raw.append((t_s, temp_c))
        while t_s - raw[0][0] > self.smooth_s:
            raw.popleft()
        if t_s - raw[0][0] < 0.5 * self.smooth_s:
            return self.direction  # too few readings yet for the average to mean much
        centre = sum(p[0] for p in raw) / len(raw)
        avg = sum(p[1] for p in raw) / len(raw)
        self._avg.append((centre, avg))
        while centre - self._avg[0][0] > self.slope_window_s:
            self._avg.popleft()
        self.slope_c_per_min = _slope(self._avg) * 60.0
        self._follow(centre, avg)
        return self.direction

    def _follow(self, t: float, v: float) -> None:
        if self._hi is None or self._lo is None:
            self._hi = self._lo = (t, v)
            return
        if v > self._hi[1]:
            self._hi = (t, v)
        if v < self._lo[1]:
            self._lo = (t, v)
        fell = self._hi[1] - v >= self.band_c
        rose = v - self._lo[1] >= self.band_c
        if self.direction >= 0 and fell:  # heating, or not yet known: starts cooling
            self._turn(-1, self._hi, t, v)
        elif self.direction <= 0 and rose:
            self._turn(1, self._lo, t, v)

    def _turn(self, new: int, extreme: tuple[float, float], t: float, v: float) -> None:
        if self.direction != 0:
            self.segment += 1
        self.direction = new
        self.just_turned = True
        self.turn_time_s, self.turn_temp_c = extreme
        self._hi = self._lo = (t, v)  # follow the new ramp from here


def _slope(pts: deque[tuple[float, float]]) -> float:
    """Least-squares slope, or NaN with fewer than three points."""
    n = len(pts)
    if n < 3:
        return NAN
    mt = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mt) ** 2 for p in pts)
    if sxx == 0:
        return NAN
    return sum((p[0] - mt) * (p[1] - my) for p in pts) / sxx
