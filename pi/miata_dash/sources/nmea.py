"""Minimal NMEA 0183 parser for the sentences a u-blox NEO-6M sends by default.

Only RMC, VTG and GGA are used. Talker IDs (GP, GN, GL...) are ignored.
"""

from __future__ import annotations

from functools import reduce

KNOTS_TO_KPH = 1.852

Values = dict[str, tuple[object, str]]  # name -> (value, unit)


def checksum_ok(line: str) -> bool:
    if not line.startswith("$") or "*" not in line:
        return False
    body, _, given = line[1:].partition("*")
    calc = reduce(lambda acc, ch: acc ^ ord(ch), body, 0)
    try:
        return calc == int(given[:2], 16)
    except ValueError:
        return False


def _float(field: str) -> float | None:
    try:
        return float(field)
    except ValueError:
        return None


def _coord(value: str, hemisphere: str) -> float | None:
    """Convert NMEA ddmm.mmmm / dddmm.mmmm to signed decimal degrees."""
    raw = _float(value)
    if raw is None:
        return None
    degrees = int(raw // 100)
    decimal = degrees + (raw - degrees * 100) / 60
    return -decimal if hemisphere in ("S", "W") else decimal


def _rmc(f: list[str]) -> Values:
    if len(f) < 9:
        return {}
    valid = f[2] == "A"
    out: Values = {"fix": (valid, "")}
    if not valid:
        return out
    knots = _float(f[7])
    if knots is not None:
        out["speed_kph"] = (knots * KNOTS_TO_KPH, "kph")
    course = _float(f[8])
    if course is not None:
        out["course"] = (course, "deg")
    lat, lon = _coord(f[3], f[4]), _coord(f[5], f[6])
    if lat is not None and lon is not None:
        out["latitude"] = (lat, "deg")
        out["longitude"] = (lon, "deg")
    if len(f[1]) >= 6 and len(f) > 9 and len(f[9]) == 6:
        t, d = f[1], f[9]
        century = "19" if int(d[4:6]) >= 80 else "20"
        out["utc"] = (f"{century}{d[4:6]}-{d[2:4]}-{d[0:2]}T{t[0:2]}:{t[2:4]}:{t[4:]}Z", "")
    return out


def _vtg(f: list[str]) -> Values:
    if len(f) < 8:
        return {}
    kph = _float(f[7])
    return {"speed_kph": (kph, "kph")} if kph is not None else {}


def _gga(f: list[str]) -> Values:
    if len(f) < 10:
        return {}
    out: Values = {}
    if f[7].isdigit():
        out["satellites"] = (int(f[7]), "")
    altitude = _float(f[9])
    if altitude is not None and f[6] not in ("", "0"):
        out["altitude"] = (altitude, "m")
    return out


_PARSERS = {"RMC": _rmc, "VTG": _vtg, "GGA": _gga}


def parse_sentence(line: str) -> Values:
    """Return the values in one NMEA line, or ``{}`` if unknown or corrupt."""
    line = line.strip()
    if not checksum_ok(line):
        return {}
    fields = line[1:].split("*")[0].split(",")
    parser = _PARSERS.get(fields[0][-3:])
    return parser(fields) if parser else {}
