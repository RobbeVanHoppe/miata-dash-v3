from __future__ import annotations

from typing import Callable, Protocol

import serial

from ..state import VehicleState
from .base import DataSource
from .nmea import parse_sentence
from .ubx import setup_sequence


class LineReader(Protocol):
    def readline(self) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def close(self) -> None: ...


class GpsSource(DataSource):
    """Reads NMEA from a serial GPS (GY-GPS6MV2 / NEO-6M) into ``gps.*`` signals.

    The NEO-6M ships at 9600 baud, 1 Hz. With ``rate_hz`` set, it is switched
    to RMC + GGA only at that rate (max 5 Hz) every time the port opens.
    ``serial_factory`` is injectable so tests and the simulator can feed lines
    without hardware.
    """

    name = "gps"

    def __init__(
        self,
        state: VehicleState,
        port: str = "/dev/serial0",
        baudrate: int = 9600,
        serial_factory: Callable[..., LineReader] = serial.Serial,
        prefix: str = "gps.",
        rate_hz: float | None = 5.0,
    ) -> None:
        super().__init__(state)
        self.port = port
        self.rate_hz = rate_hz
        self.baudrate = baudrate
        self.prefix = prefix
        self._serial_factory = serial_factory
        self._serial: LineReader | None = None

    def open(self) -> None:
        self._serial = self._serial_factory(self.port, self.baudrate, timeout=1)
        if self.rate_hz:
            for msg in setup_sequence(self.rate_hz):
                self._serial.write(msg)

    def poll(self) -> None:
        raw = self._serial.readline()
        if raw:
            self.handle_line(raw.decode("ascii", errors="replace"))

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def handle_line(self, line: str) -> None:
        for name, (value, unit) in parse_sentence(line).items():
            self.state.update(self.prefix + name, value, unit)
