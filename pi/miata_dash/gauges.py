"""What the dashboard shows. Scales are display ranges only, not limits or warnings."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Gauge:
    id: str
    label: str
    signal: str  # key in VehicleState
    unit: str
    minimum: float
    maximum: float
    decimals: int = 0
    fallback: tuple[str, ...] = ()  # used in order when ``signal`` is missing or stale
    dial_step: float = 0  # major tick spacing on round dials; 0 = automatic
    dial_label_div: float = 1  # e.g. 1000 so the RPM dial reads 1..8

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signals"] = [self.signal, *self.fallback]
        return d


DEFAULT_GAUGES: tuple[Gauge, ...] = (
    Gauge("rpm", "RPM", "ecu.RPM", "rpm", 0, 8000, dial_step=1000, dial_label_div=1000),
    # Fused GPS/IMU speed, falling back to raw GPS when no IMU is fitted.
    Gauge("speed", "Speed", "ins.speed_kph", "km/h", 0, 220, fallback=("gps.speed_kph",), dial_step=20),
    Gauge("clt", "Coolant", "ecu.CLT", "°C", -20, 130, dial_step=30),
    Gauge("iat", "Intake air", "ecu.IAT", "°C", -20, 80, dial_step=20),
    Gauge("map", "MAP", "ecu.MAP", "kPa", 0, 250, dial_step=50),
    Gauge("tps", "Throttle", "ecu.TPS", "%", 0, 100, dial_step=25),
    Gauge("vbat", "Battery", "ecu.VBAT", "V", 8, 16, decimals=1, dial_step=2),
    Gauge("afr", "AFR", "ecu.AFRCurr_1", "", 10, 20, decimals=2, dial_step=2),
    Gauge("oil_p", "Oil pressure", "ecu.Oil_P", "kPa", 0, 700, dial_step=100),
    Gauge("oil_t", "Oil temp", "ecu.Oil_T", "°C", 0, 150, dial_step=30),
)

LAYOUTS = ("digital", "retro", "motorsport")
