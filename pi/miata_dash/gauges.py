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

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_GAUGES: tuple[Gauge, ...] = (
    Gauge("rpm", "RPM", "ecu.RPM", "rpm", 0, 8000),
    Gauge("speed", "Speed", "gps.speed_kph", "km/h", 0, 220),
    Gauge("clt", "Coolant", "ecu.CLT", "°C", -20, 130),
    Gauge("iat", "Intake air", "ecu.IAT", "°C", -20, 80),
    Gauge("map", "MAP", "ecu.MAP", "kPa", 0, 250),
    Gauge("tps", "Throttle", "ecu.TPS", "%", 0, 100),
    Gauge("vbat", "Battery", "ecu.VBAT", "V", 8, 16, decimals=1),
    Gauge("afr", "AFR", "ecu.AFRCurr_1", "", 10, 20, decimals=2),
    Gauge("oil_p", "Oil pressure", "ecu.Oil_P", "kPa", 0, 700),
    Gauge("oil_t", "Oil temp", "ecu.Oil_T", "°C", 0, 150),
)
