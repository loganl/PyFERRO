"""Small numeric helpers used by the acquisition loop."""

from __future__ import annotations

from collections import deque


class DirectionTracker:
    """Classify the temperature ramp as heating (+1), cooling (-1) or steady (0).

    Uses a least-squares slope over the last ``window_s`` seconds with a
    hysteresis threshold so noise near a turning point does not flicker.
    ``segment`` increments each time the ramp flips between heating and cooling.
    """

    def __init__(self, window_s: float = 60.0, threshold_c_per_min: float = 0.2) -> None:
        self.window_s = window_s
        self.threshold = threshold_c_per_min / 60.0
        self._pts: deque[tuple[float, float]] = deque()
        self.direction = 0
        self.segment = 0
        self._last_moving = 0
        self.slope_c_per_min = float("nan")

    def update(self, t_s: float, temp_c: float) -> int:
        if temp_c != temp_c:  # NaN: keep the previous state
            return self.direction
        self._pts.append((t_s, temp_c))
        while self._pts and t_s - self._pts[0][0] > self.window_s:
            self._pts.popleft()
        if len(self._pts) < 3 or self._pts[-1][0] - self._pts[0][0] < min(10.0, self.window_s / 2):
            return self.direction
        n = len(self._pts)
        mt = sum(p[0] for p in self._pts) / n
        my = sum(p[1] for p in self._pts) / n
        sxx = sum((p[0] - mt) ** 2 for p in self._pts)
        if sxx == 0:
            return self.direction
        slope = sum((p[0] - mt) * (p[1] - my) for p in self._pts) / sxx
        self.slope_c_per_min = slope * 60.0
        if slope > self.threshold:
            new = 1
        elif slope < -self.threshold:
            new = -1
        elif abs(slope) < self.threshold / 2:
            new = 0
        else:
            new = self.direction
        if new != 0 and new != self._last_moving:
            if self._last_moving != 0:
                self.segment += 1
            self._last_moving = new
        self.direction = new
        return new
