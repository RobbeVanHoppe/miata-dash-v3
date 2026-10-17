"""MPU6050 (GY-521 module) on the Pi's I²C bus.

Register map and scale factors from the InvenSense MPU-6000/6050 register map
(rev 4.2): WHO_AM_I = 0x68, accel ±2 g = 16384 LSB/g, gyro ±250 °/s = 131 LSB/(°/s),
temperature = raw / 340 + 36.53 °C.
"""

from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from ..state import VehicleState
from .base import DataSource

G = 9.80665  # m/s²

ADDR = 0x68
REG_SMPLRT_DIV = 0x19
REG_CONFIG = 0x1A
REG_GYRO_CONFIG = 0x1B
REG_ACCEL_CONFIG = 0x1C
REG_ACCEL_XOUT_H = 0x3B
REG_PWR_MGMT_1 = 0x6B
REG_WHO_AM_I = 0x75

ACCEL_LSB_PER_G = 16384.0
GYRO_LSB_PER_DPS = 131.0


class I2cBus(Protocol):
    def write_byte_data(self, addr: int, reg: int, value: int) -> None: ...
    def read_byte_data(self, addr: int, reg: int) -> int: ...
    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class ImuSample:
    t: float  # monotonic seconds
    accel: tuple[float, float, float]  # m/s², sensor frame
    gyro: tuple[float, float, float]  # °/s, sensor frame
    temp_c: float


class Mpu6050:
    def __init__(self, bus: I2cBus, addr: int = ADDR) -> None:
        self.bus = bus
        self.addr = addr

    def init(self) -> None:
        who = self.bus.read_byte_data(self.addr, REG_WHO_AM_I)
        if who != 0x68:
            raise OSError(f"MPU6050 not found (WHO_AM_I=0x{who:02X})")
        self.bus.write_byte_data(self.addr, REG_PWR_MGMT_1, 0x01)  # wake, PLL on gyro X
        self.bus.write_byte_data(self.addr, REG_CONFIG, 0x03)  # DLPF ~44 Hz: cuts engine vibration
        self.bus.write_byte_data(self.addr, REG_SMPLRT_DIV, 9)  # 1 kHz / (1 + 9) = 100 Hz
        self.bus.write_byte_data(self.addr, REG_GYRO_CONFIG, 0x00)  # ±250 °/s
        self.bus.write_byte_data(self.addr, REG_ACCEL_CONFIG, 0x00)  # ±2 g

    def read(self, now: float) -> ImuSample:
        raw = bytes(self.bus.read_i2c_block_data(self.addr, REG_ACCEL_XOUT_H, 14))
        ax, ay, az, temp, gx, gy, gz = struct.unpack(">7h", raw)
        k = G / ACCEL_LSB_PER_G
        return ImuSample(
            t=now,
            accel=(ax * k, ay * k, az * k),
            gyro=(gx / GYRO_LSB_PER_DPS, gy / GYRO_LSB_PER_DPS, gz / GYRO_LSB_PER_DPS),
            temp_c=temp / 340.0 + 36.53,
        )


_AXES = {"x": 0, "y": 1, "z": 2}


def axis_vector(spec: str) -> tuple[float, float, float]:
    """'+x', '-z', ... -> unit vector in the sensor frame."""
    sign = -1.0 if spec[0] == "-" else 1.0
    v = [0.0, 0.0, 0.0]
    v[_AXES[spec[-1]]] = sign
    return tuple(v)  # type: ignore[return-value]


def dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def normalize(v):
    n = math.sqrt(dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n)


class Mounting:
    """Rotates sensor-frame vectors into the car frame (forward, left, up).

    The nominal orientation comes from settings (which sensor axis points
    forward / up). ``level(gravity)`` replaces "up" by the measured gravity
    direction while parked, so a slightly tilted mounting does not read as a
    constant acceleration.
    """

    def __init__(self, forward: str = "+x", up: str = "+z") -> None:
        self.set_axes(axis_vector(forward), axis_vector(up))

    def set_axes(self, forward, up) -> None:
        up = normalize(up)
        f = (forward[0] - dot(forward, up) * up[0],
             forward[1] - dot(forward, up) * up[1],
             forward[2] - dot(forward, up) * up[2])
        self.forward = normalize(f)
        self.up = up
        self.left = cross(self.up, self.forward)

    def level(self, gravity_specific_force) -> None:
        """At rest the accelerometer reads +1 g along "up"."""
        self.set_axes(self.forward, gravity_specific_force)

    def to_car(self, v) -> tuple[float, float, float]:
        return (dot(v, self.forward), dot(v, self.left), dot(v, self.up))


class ImuSource(DataSource):
    """Reads the MPU6050 at ~100 Hz.

    Stores car-frame values (``imu.a_fwd`` etc.) at a reduced rate to keep the
    state lock quiet, and hands every sample to ``on_sample`` (the speed fusion).
    """

    name = "imu"
    period = 0.01
    publish_every = 10  # samples -> 10 Hz into VehicleState

    def __init__(
        self,
        state: VehicleState,
        mounting: Mounting,
        bus_factory: Callable[[], I2cBus] | None = None,
        on_sample: Callable[[ImuSample], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(state)
        self.mounting = mounting
        self._bus_factory = bus_factory or _smbus_factory
        self.on_sample = on_sample
        self.clock = clock
        self._bus: I2cBus | None = None
        self._imu: Mpu6050 | None = None
        self._count = 0

    def open(self) -> None:
        self._bus = self._bus_factory()
        self._imu = Mpu6050(self._bus)
        self._imu.init()

    def close(self) -> None:
        if self._bus is not None:
            self._bus.close()
            self._bus = self._imu = None

    def poll(self) -> None:
        self._stop.wait(self.period)
        sample = self._imu.read(self.clock())
        if self.on_sample is not None:
            self.on_sample(sample)
        self._count += 1
        if self._count % self.publish_every == 0:
            self.publish(sample)

    def publish(self, sample: ImuSample) -> None:
        a = self.mounting.to_car(sample.accel)
        w = self.mounting.to_car(sample.gyro)
        for name, value in (("a_fwd", a[0]), ("a_left", a[1]), ("a_up", a[2])):
            self.state.update("imu." + name, value, "m/s²")
        self.state.update("imu.yaw_rate", w[2], "deg/s")
        self.state.update("imu.temp", sample.temp_c, "deg C")


def _smbus_factory() -> I2cBus:
    from smbus2 import SMBus  # Pi only; imported lazily so the PC simulator works without it

    return SMBus(1)
