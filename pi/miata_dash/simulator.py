"""Fake ECU and GPS for running the dashboard on a PC without the car.

The fake ECU encodes frames with the real DBC and sends them on a python-can
``virtual`` bus, so the full CanSource decode path is exercised.
"""

from __future__ import annotations

import math
import struct
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


TUNNEL_EVERY_S = 90.0  # the fake GPS loses its fix for TUNNEL_S out of every 90 s
TUNNEL_S = 12.0


def _speed_kph(t: float) -> float:
    return _engine(t)["speed_kph"]


def _accel_fwd(t: float) -> float:
    """d(speed)/dt of the drive cycle, in m/s²."""
    # speed_kph = 120 · (sin(t/4) + 1) / 2  →  dv/dt = 120/3.6 · cos(t/4) / 8
    return 120 / 3.6 * math.cos(t / 4) / 8


def in_tunnel(t: float) -> bool:
    return t % TUNNEL_EVERY_S >= TUNNEL_EVERY_S - TUNNEL_S


class FakeSerialGps:
    """Stands in for ``serial.Serial``: RMC + GGA at 5 Hz, with a periodic tunnel."""

    period = 0.2

    def __init__(self, *_args, start: float | None = None, **_kwargs) -> None:
        self._start = time.monotonic() if start is None else start
        self._queue: list[str] = []
        self._next = 0.0
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def readline(self) -> bytes:
        if not self._queue:
            now = time.monotonic()
            if now < self._next:
                time.sleep(min(0.05, self._next - now))
                return b""
            self._next = now + self.period
            t = now - self._start
            stamp = time.strftime("%H%M%S.00", time.gmtime())
            date = time.strftime("%d%m%y", time.gmtime())
            if in_tunnel(t):
                self._queue = [
                    _with_checksum(f"GPRMC,{stamp},V,,,,,,,{date},,,N"),
                    _with_checksum(f"GPGGA,{stamp},,,,,0,00,99.9,,M,,M,,"),
                ]
            else:
                knots = _speed_kph(t) * KPH_TO_KNOTS
                self._queue = [
                    _with_checksum(f"GPRMC,{stamp},A,0000.000,N,00000.000,E,{knots:.2f},90.0,{date},,,A"),
                    _with_checksum(f"GPGGA,{stamp},0000.000,N,00000.000,E,1,08,1.0,10.0,M,47.0,M,,"),
                ]
        return self._queue.pop(0).encode("ascii")

    def close(self) -> None:
        pass


class FakeI2cBus:
    """Stands in for smbus2.SMBus with an MPU6050 mounted +x forward, +z up."""

    def __init__(self, start: float | None = None) -> None:
        self._start = time.monotonic() if start is None else start

    def write_byte_data(self, addr: int, reg: int, value: int) -> None:
        pass

    def read_byte_data(self, addr: int, reg: int) -> int:
        return 0x68 if reg == 0x75 else 0

    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        t = time.monotonic() - self._start
        lsb = 16384 / 9.80665
        ax = round(_accel_fwd(t) * lsb + 40)  # +40 LSB: a small bias for the filter to learn
        az = 16384  # +1 g up
        raw = struct.pack(">7h", ax, 0, az, 0, 0, 0, 0)
        return list(raw)

    def close(self) -> None:
        pass


class FakePipeline:
    def __init__(self) -> None:
        self.stopped = False

    def poll(self) -> int | None:
        return 0 if self.stopped else None

    def stop(self) -> None:
        self.stopped = True


class FakeRadioRunner:
    """Pretends rtl_fm | aplay runs; rtl_power "finds" three stations."""

    STATIONS_MHZ = (89.5, 94.2, 101.3)

    def __init__(self) -> None:
        self.started: list[tuple[list[str], list[str]]] = []

    def start_pipeline(self, producer: list[str], consumer: list[str]) -> FakePipeline:
        self.started.append((producer, consumer))
        return FakePipeline()

    def run(self, cmd: list[str], timeout: float) -> str:
        step = 50_000
        lo = 87_500_000
        levels = []
        for i in range(int((108_000_000 - lo) / step) + 1):
            f = lo + i * step
            near = min(abs(f - s * 1e6) for s in self.STATIONS_MHZ)
            levels.append(-30.0 if near < step / 2 else -45.0)
        return f"2026-10-17, 22:00:00, {lo}, 108000000, {step}, 16, " + ", ".join(map(str, levels)) + "\n"


class FakeMainNode:
    """Main node on the virtual bus: echoes the Pi's relay requests in MAIN_STATUS
    and releases them when PI_MAIN_CMD stops (heartbeat timeout), like the firmware."""

    timeout_s = 1.0

    def __init__(self, net_dbc_path: str | Path, channel: str) -> None:
        self.db = cantools.database.load_file(str(net_dbc_path))
        self.channel = channel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fake-main", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)

    def _run(self) -> None:
        cmd = self.db.get_message_by_name("PI_MAIN_CMD")
        status = self.db.get_message_by_name("MAIN_STATUS")
        relays = {"O02": 0, "O03": 0, "O04": 0}
        last_cmd = -1e9
        counter = 0
        next_tx = time.monotonic()
        with can.Bus(interface="virtual", channel=self.channel) as bus:
            while not self._stop.is_set():
                msg = bus.recv(timeout=0.02)
                now = time.monotonic()
                if msg is not None and msg.arbitration_id == cmd.frame_id:
                    d = cmd.decode(msg.data)
                    relays = {r: d[f"Pi_RelayReq_{r}"] for r in relays}
                    last_cmd = now
                timeout = now - last_cmd > self.timeout_s
                if timeout:
                    relays = dict.fromkeys(relays, 0)
                if now >= next_tx:
                    next_tx = now + 0.1
                    counter = (counter + 1) & 0xFF
                    bus.send(can.Message(arbitration_id=status.frame_id, is_extended_id=False, data=status.encode({
                        "Main_IG1": 1, "Main_HeadSwitch": 0, "Main_Reverse": 0, "Main_LightsOffLatched": 0,
                        **{f"Main_Relay_{r}": v for r, v in relays.items()},
                        "Main_PiTimeout": int(timeout), "Main_Faults": 0, "Main_VBat": 13.9,
                        "Main_Counter": counter,
                    })))
