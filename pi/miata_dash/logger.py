"""Per-drive CSV logging.

A drive starts when the engine is seen running (fresh ``ecu.RPM`` > 0) and
ends ``end_after_s`` seconds after it stops, so a stall or a short stop at a
fuel station stays in the same file. Files are numbered so they sort in
order even before the Pi knows the real time (no RTC until GPS or network).
"""

from __future__ import annotations

import csv
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, TextIO

from .settings import SettingsStore
from .sources.base import DataSource
from .state import VehicleState

log = logging.getLogger(__name__)

_SEQ = re.compile(r"^(\d{4,})_")


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)  # drops float noise; 1e-6 deg is ~0.1 m
    return value


class DriveLogger(DataSource):
    name = "logger"
    max_age = 2.0  # s; older values are written as empty cells
    fsync_every_s = 5.0  # bounds data lost on power cut without wearing the SD card

    def __init__(
        self,
        state: VehicleState,
        store: SettingsStore,
        log_dir: str | Path,
        columns: list[str],
        rate_hz: float = 10.0,
        end_after_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(state)
        self.store = store
        self.log_dir = Path(log_dir)
        self.columns = columns
        self.period = 1.0 / rate_hz
        self.end_after_s = end_after_s
        self.clock = clock
        self.path: Path | None = None
        self._file: TextIO | None = None
        self._writer = None
        self._start = 0.0
        self._last_running = 0.0
        self._last_sync = 0.0

    @property
    def active(self) -> bool:
        return self._file is not None

    def open(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> None:
        self._stop.wait(self.period)
        self.sample()

    def close(self) -> None:
        self._end_drive()

    def sample(self) -> None:
        now = self.clock()
        if not self.store.settings.logging_enabled:
            self._end_drive()
            return
        rpm = self.state.value("ecu.RPM", max_age=self.max_age)
        if isinstance(rpm, (int, float)) and rpm > 0:
            self._last_running = now
            if not self.active:
                self._start_drive(now)
        elif self.active and now - self._last_running > self.end_after_s:
            self._end_drive()
        if self.active:
            self._write_row(now)

    def _next_path(self) -> Path:
        seqs = [int(m.group(1)) for p in self.log_dir.glob("*.csv") if (m := _SEQ.match(p.name))]
        seq = max(seqs, default=0) + 1
        return self.log_dir / f"{seq:04d}_{time.strftime('%Y%m%d-%H%M%S')}.csv"

    def _start_drive(self, now: float) -> None:
        self.path = self._next_path()
        self._file = open(self.path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._writer.writerow(["t_s", *self.columns])
        self._start = self._last_sync = now
        log.info("logger: drive started, %s", self.path.name)

    def _write_row(self, now: float) -> None:
        values = (self.state.value(c, max_age=self.max_age) for c in self.columns)
        self._writer.writerow([f"{now - self._start:.2f}", *(_cell(v) for v in values)])
        if now - self._last_sync >= self.fsync_every_s:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._last_sync = now

    def _end_drive(self) -> None:
        if self._file is None:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        log.info("logger: drive ended, %s", self.path.name)
        self._file = self._writer = None
