import pytest

from miata_dash.sources.nmea import checksum_ok, parse_sentence
from miata_dash.simulator import _with_checksum

# Reference sentences with valid checksums (NMEA 0183 examples).
RMC = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def test_checksum():
    assert checksum_ok(RMC)
    assert not checksum_ok(RMC.replace("022.4", "023.4"))
    assert not checksum_ok("GPRMC,no,dollar*00")


def test_rmc():
    v = parse_sentence(RMC)
    assert v["fix"] == (True, "")
    assert v["speed_kph"][0] == pytest.approx(22.4 * 1.852)
    assert v["latitude"][0] == pytest.approx(48 + 7.038 / 60)
    assert v["longitude"][0] == pytest.approx(11 + 31.0 / 60)
    assert v["utc"][0] == "1994-03-23T12:35:19Z"


def test_rmc_without_fix_has_no_speed():
    v = parse_sentence(_with_checksum("GPRMC,123519,V,,,,,,,230394,,,N").strip())
    assert v == {"fix": (False, "")}


def test_southern_western_hemisphere_is_negative():
    v = parse_sentence(_with_checksum("GNRMC,000000,A,3345.000,S,07040.000,W,0.0,0.0,010125,,,A"))
    assert v["latitude"][0] < 0 and v["longitude"][0] < 0


def test_gga():
    v = parse_sentence(GGA)
    assert v["satellites"] == (8, "")
    assert v["altitude"] == (545.4, "m")


def test_vtg_and_unknown():
    assert parse_sentence(_with_checksum("GPVTG,054.7,T,034.4,M,005.5,N,010.2,K,A")) == {
        "speed_kph": (10.2, "kph")
    }
    assert parse_sentence(_with_checksum("GPGSV,1,1,00")) == {}
