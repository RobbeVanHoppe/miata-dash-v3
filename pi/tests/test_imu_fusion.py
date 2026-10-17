import math
import struct

import pytest

from miata_dash.fusion import KPH, SpeedEstimator
from miata_dash.sources import ubx
from miata_dash.sources.gps_source import GpsSource
from miata_dash.sources.imu import G, ImuSample, Mounting, Mpu6050
from miata_dash.simulator import FakeSerialGps
from miata_dash.state import VehicleState


# -- UBX -------------------------------------------------------------------
def test_cfg_rate_5hz_matches_known_frame():
    # Widely published NEO-6M 5 Hz command (u-center): B5 62 06 08 06 00 C8 00 01 00 01 00 DE 6A
    assert ubx.cfg_rate(5) == bytes.fromhex("B562060806 00C8000100010 0DE6A".replace(" ", ""))


def test_setup_keeps_only_rmc_and_gga():
    msgs = ubx.setup_sequence(5)
    rates = {m[7]: m[8] for m in msgs if m[3] == ubx.ID_CFG_MSG}  # NMEA id -> rate
    assert rates == {0x00: 1, 0x01: 0, 0x02: 0, 0x03: 0, 0x04: 1, 0x05: 0}
    for m in msgs:
        assert ubx.checksum(m[2:-2]) == m[-2:]


def test_gps_source_sends_setup_on_open():
    source = GpsSource(VehicleState(), serial_factory=FakeSerialGps, rate_hz=5)
    source.open()
    written = bytes(source._serial.written)
    source.close()
    assert written.endswith(ubx.cfg_rate(5))


# -- MPU6050 ---------------------------------------------------------------
class FakeBus:
    def __init__(self, who=0x68, raw=(0, 0, 16384, 0, 131, 0, -262)):
        self.who = who
        self.raw = raw
        self.writes = []

    def write_byte_data(self, addr, reg, value):
        self.writes.append((reg, value))

    def read_byte_data(self, addr, reg):
        return self.who

    def read_i2c_block_data(self, addr, reg, length):
        return list(struct.pack(">7h", *self.raw))

    def close(self):
        pass


def test_mpu6050_scaling():
    imu = Mpu6050(FakeBus())
    imu.init()
    s = imu.read(1.0)
    assert s.accel[2] == pytest.approx(G)
    assert s.gyro == pytest.approx((1.0, 0.0, -2.0))
    assert s.temp_c == pytest.approx(36.53)


def test_mpu6050_wrong_chip():
    with pytest.raises(OSError):
        Mpu6050(FakeBus(who=0x00)).init()


def test_mounting_axes_and_levelling():
    m = Mounting(forward="-y", up="+x")
    assert m.to_car((0, -1, 0)) == pytest.approx((1, 0, 0))
    assert m.to_car((1, 0, 0)) == pytest.approx((0, 0, 1))
    # Mounted 5° nose-up: gravity reads partly on the sensor's forward axis.
    tilt = math.radians(5)
    m = Mounting("+x", "+z")
    m.level((G * math.sin(tilt), 0, G * math.cos(tilt)))
    assert m.to_car((G * math.sin(tilt), 0, G * math.cos(tilt)))[0] == pytest.approx(0, abs=1e-9)


# -- fusion ----------------------------------------------------------------
class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def run(est, state, clock, seconds, accel, speed_fn=None, gps_hz=5, imu_hz=100):
    """Advance time; feed IMU every sample, GPS at gps_hz when speed_fn gives a value."""
    steps = int(seconds * imu_hz)
    for i in range(steps):
        clock.now += 1 / imu_hz
        if speed_fn is not None and i % (imu_hz // gps_hz) == 0:
            v = speed_fn(clock.now)
            if v is not None:
                state.update("gps.fix", True)
                state.update("gps.speed_kph", v * KPH)
            else:
                state.update("gps.fix", False)
        est.on_imu(ImuSample(clock.now, (accel(clock.now), 0.0, G), (0.0, 0.0, 0.0), 25.0))


def make():
    clock = Clock()
    state = VehicleState(clock)
    return clock, state, SpeedEstimator(state, Mounting())


def test_tracks_gps_and_learns_bias():
    clock, state, est = make()
    bias = 0.15  # m/s² accelerometer offset
    run(est, state, clock, 30, accel=lambda t: 1.0 + bias, speed_fn=lambda t: 1.0 * t)
    assert est.v == pytest.approx(30.0, abs=0.3)
    assert est.b == pytest.approx(bias, abs=0.05)
    assert est.mode(clock.now) == "gps"


def test_bridges_a_tunnel_then_gives_up():
    clock, state, est = make()
    bias = 0.1
    run(est, state, clock, 30, accel=lambda t: bias, speed_fn=lambda t: 25.0)  # cruise 90 km/h
    t0 = clock.now
    # Tunnel: GPS lost, car decelerates at 0.5 m/s² for 10 s.
    run(est, state, clock, 10, accel=lambda t: -0.5 + bias, speed_fn=lambda t: None)
    assert est.mode(clock.now) == "dr"
    assert est.v == pytest.approx(25.0 - 0.5 * (clock.now - t0), abs=1.0)
    assert state.value("ins.mode") == "dr"
    # Much longer without GPS: estimate withheld.
    run(est, state, clock, 60, accel=lambda t: bias, speed_fn=lambda t: None)
    assert est.mode(clock.now) == "lost"


def test_speed_never_negative():
    clock, state, est = make()
    run(est, state, clock, 5, accel=lambda t: 0.0, speed_fn=lambda t: 0.5)
    run(est, state, clock, 5, accel=lambda t: -2.0, speed_fn=lambda t: None)
    assert est.v >= 0.0
