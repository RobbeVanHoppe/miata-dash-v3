"""GPS/INS speed fusion: GPS speed corrected between fixes and bridged through
dropouts (tunnels) by integrating the IMU's forward acceleration.

Two-state Kalman filter, x = [v, b]:
    v  speed along the car's forward axis (m/s)
    b  accelerometer bias along that axis (m/s²); it also absorbs road slope,
       since a parked car on a hill reads g·sin(slope) forward.
Predict (every IMU sample, a = forward specific force):
    v ← v + (a − b)·dt,   b ← b
Update (every fresh GPS fix):  z = v_gps,  H = [1 0]
Dead reckoning is reported as lost once σ_v exceeds ``max_sigma`` or no GPS
arrived for ``max_dr_s``: drift grows with t², so long bridges are not trusted.
"""

from __future__ import annotations

import math
import threading
from collections import deque

from .sources.imu import ImuSample, Mounting
from .state import VehicleState

KPH = 3.6


class SpeedEstimator:
    accel_noise = 0.5  # m/s², vibration + vehicle pitch; process noise on v
    bias_walk = 0.02  # m/s² per √s; slope changes, temperature drift
    gps_noise = 0.3  # m/s, NEO-6M speed accuracy class (~0.1 m/s spec) with margin
    gps_max_age = 1.5  # s
    max_sigma = 3.0  # m/s (≈ 11 km/h) — beyond this the estimate is withheld
    max_dr_s = 30.0  # s without GPS before dead reckoning is abandoned
    level_after_s = 2.0  # s parked (GPS) before re-levelling the mounting
    publish_period = 0.1  # s

    def __init__(self, state: VehicleState, mounting: Mounting) -> None:
        self.state = state
        self.mounting = mounting
        self._lock = threading.Lock()
        self.v = 0.0
        self.b = 0.0
        self.P = [[100.0, 0.0], [0.0, 1.0]]  # unknown at start
        self.initialised = False
        self._last_t: float | None = None
        self._last_gps_ts: float | None = None
        self._last_gps_t: float | None = None
        self._last_publish = -math.inf
        self._parked: deque[tuple[float, tuple[float, float, float]]] = deque()

    # -- filter steps -------------------------------------------------------
    def predict(self, a_fwd: float, dt: float) -> None:
        P = self.P
        self.v = max(0.0, self.v + (a_fwd - self.b) * dt)
        # P = F P Fᵀ + Q with F = [[1, -dt], [0, 1]]
        p00 = P[0][0] - dt * (P[1][0] + P[0][1]) + dt * dt * P[1][1] + (self.accel_noise * dt) ** 2
        p01 = P[0][1] - dt * P[1][1]
        p11 = P[1][1] + self.bias_walk**2 * dt
        self.P = [[p00, p01], [p01, p11]]

    def update_gps(self, v_gps: float) -> None:
        P = self.P
        s = P[0][0] + self.gps_noise**2
        k0, k1 = P[0][0] / s, P[1][0] / s
        y = v_gps - self.v
        self.v = max(0.0, self.v + k0 * y)
        self.b += k1 * y
        self.P = [
            [(1 - k0) * P[0][0], (1 - k0) * P[0][1]],
            [P[1][0] - k1 * P[0][0], P[1][1] - k1 * P[0][1]],
        ]
        self.initialised = True

    @property
    def sigma(self) -> float:
        return math.sqrt(max(self.P[0][0], 0.0))

    # -- wiring -------------------------------------------------------------
    def on_imu(self, sample: ImuSample) -> None:
        """Called from the IMU thread for every sample."""
        with self._lock:
            if self._last_t is not None:
                dt = min(max(sample.t - self._last_t, 0.0), 0.1)
                self.predict(self.mounting.to_car(sample.accel)[0], dt)
            self._last_t = sample.t
            self._check_gps(sample.t)
            self._auto_level(sample)
            if sample.t - self._last_publish >= self.publish_period:
                self._last_publish = sample.t
                self._publish(sample.t)

    def _check_gps(self, now: float) -> None:
        reading = self.state.get("gps.speed_kph")
        fix = self.state.value("gps.fix", max_age=self.gps_max_age)
        if reading is None or fix is not True or reading.timestamp == self._last_gps_ts:
            return
        if now - reading.timestamp > self.gps_max_age:
            return
        self._last_gps_ts = reading.timestamp
        self._last_gps_t = now
        self.update_gps(float(reading.value) / KPH)

    def _auto_level(self, sample: ImuSample) -> None:
        gps = self.state.value("gps.speed_kph", max_age=self.gps_max_age)
        gyro_calm = max(abs(w) for w in sample.gyro) < 2.0  # °/s
        if gps is None or gps > 1.0 or not gyro_calm:
            self._parked.clear()
            return
        self._parked.append((sample.t, sample.accel))
        if sample.t - self._parked[0][0] >= self.level_after_s:
            n = len(self._parked)
            mean = tuple(sum(a[i] for _, a in self._parked) / n for i in range(3))
            self.mounting.level(mean)
            self.v = 0.0
            self._parked.clear()

    def mode(self, now: float) -> str:
        if not self.initialised:
            return "lost"
        if self._last_gps_t is not None and now - self._last_gps_t <= self.gps_max_age:
            return "gps"
        dr_time = math.inf if self._last_gps_t is None else now - self._last_gps_t
        if dr_time <= self.max_dr_s and self.sigma <= self.max_sigma:
            return "dr"
        return "lost"

    def _publish(self, now: float) -> None:
        mode = self.mode(now)
        self.state.update("ins.mode", mode)
        self.state.update("ins.sigma_kph", self.sigma * KPH, "kph")
        if mode != "lost":
            self.state.update("ins.speed_kph", self.v * KPH, "kph")
