"""Thread-safe store holding the latest value of every signal.

Sources write into it from their own threads; the web layer reads snapshots.
Signal names are namespaced by source, e.g. ``ecu.RPM`` or ``gps.speed_kph``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Union

Value = Union[float, int, bool, str, None]


@dataclass(frozen=True)
class Reading:
    value: Value
    unit: str
    timestamp: float  # clock() time of the update, not wall time

    def age(self, now: float) -> float:
        return now - self.timestamp


class VehicleState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._readings: dict[str, Reading] = {}

    def update(self, name: str, value: Value, unit: str = "") -> None:
        reading = Reading(value, unit, self._clock())
        with self._lock:
            self._readings[name] = reading

    def get(self, name: str) -> Reading | None:
        with self._lock:
            return self._readings.get(name)

    def value(self, name: str, max_age: float | None = None, default: Value = None) -> Value:
        """Latest value, or ``default`` if missing or older than ``max_age`` seconds."""
        reading = self.get(name)
        if reading is None:
            return default
        if max_age is not None and reading.age(self._clock()) > max_age:
            return default
        return reading.value

    def snapshot(self) -> dict[str, dict]:
        now = self._clock()
        with self._lock:
            items = list(self._readings.items())
        return {
            name: {"value": r.value, "unit": r.unit, "age": round(r.age(now), 3)}
            for name, r in items
        }
