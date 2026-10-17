"""Fake ECU and GPS for running the dashboard on a PC without the car.

The fake ECU encodes frames with the real DBC and sends them on a python-can
``virtual`` bus, so the full CanSource decode path is exercised.
"""

from __future__ import annotations

import math
import threading
import time
from functools import reduce
from pathlib import Path

import can
import cantools

KPH_TO_KNOTS = 1 / 1.852


def _engine(t: float) -> dict[str, float]:
    """A slow, repeating drive cycle: values are plausible, not measured."""
    load = (math.sin(t / 4) + 1) / 2  # 0..1
    return {
        "RPM": 900 + 5500 * load,
        "TPS": 100 * load,
        "MAP": 35 + 65 * load,
        "IAT": 25 + 5 * load,
        "CLT": min(90.0, 20 + t),
        "VBAT": 14.1 - 0.3 * load,
        "Oil_P": 150 + 300 * load,
        "Oil_T": min(100.0, 20 + t / 2),
        "AFRCurr_1": 14.7 - 2.5 * load,
        "speed_kph": 120 * load,
    }


class FakeEcu:
    def __init__(self, dbc_path: str | Path, channel: str, period: float = 0.05) -> None:
        self.db = cantools.database.load_file(str(dbc_path))
        self.channel = channel
        self.period = period
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def frames(self, t: float) -> list[can.Message]:
        values = _engine(t)
        messages = []
        for definition in self.db.messages:
            if definition.name == "ME1_In_1":
                continue  # that one is an input to the ECU, not sent by it
            # Unsimulated signals get their offset, i.e. raw 0 (AFR has offset 7.5).
            data = {s.name: values.get(s.name, s.offset) for s in definition.signals}
            payload = definition.encode(data, strict=False)
            messages.append(can.Message(arbitration_id=definition.frame_id, data=payload, is_extended_id=False))
        return messages

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fake-ecu", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)

    def _run(self) -> None:
        start = time.monotonic()
        with can.Bus(interface="virtual", channel=self.channel) as bus:
            while not self._stop.wait(self.period):
                for msg in self.frames(time.monotonic() - start):
                    bus.send(msg)


def _with_checksum(body: str) -> str:
    return f"${body}*{reduce(lambda a, c: a ^ ord(c), body, 0):02X}\r\n"


class FakeSerialGps:
    """Stands in for ``serial.Serial``: emits RMC + GGA once per second (NEO-6M default)."""

    def __init__(self, *_args, **_kwargs) -> None:
        self._start = time.monotonic()
        self._queue: list[str] = []
        self._next = 0.0

    def readline(self) -> bytes:
        if not self._queue:
            now = time.monotonic()
            if now < self._next:
                time.sleep(min(0.2, self._next - now))
                return b""
            self._next = now + 1.0
            knots = _engine(now - self._start)["speed_kph"] * KPH_TO_KNOTS
            stamp = time.strftime("%H%M%S.00", time.gmtime())
            date = time.strftime("%d%m%y", time.gmtime())
            self._queue = [
                _with_checksum(f"GPRMC,{stamp},A,0000.000,N,00000.000,E,{knots:.2f},90.0,{date},,,A"),
                _with_checksum(f"GPGGA,{stamp},0000.000,N,00000.000,E,1,08,1.0,10.0,M,47.0,M,,"),
            ]
        return self._queue.pop(0).encode("ascii")

    def close(self) -> None:
        pass
