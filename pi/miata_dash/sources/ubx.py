"""u-blox UBX configuration messages for the NEO-6M.

Settings are sent to RAM on every start (not saved to flash), so a module
that loses its backup battery comes back correctly configured anyway.
"""

from __future__ import annotations

import struct

SYNC = b"\xb5\x62"
CLASS_CFG = 0x06
ID_CFG_MSG = 0x01
ID_CFG_RATE = 0x08

# NMEA standard messages (class 0xF0) and their UBX message IDs.
NMEA_CLASS = 0xF0
NMEA_IDS = {"GGA": 0x00, "GLL": 0x01, "GSA": 0x02, "GSV": 0x03, "RMC": 0x04, "VTG": 0x05}


def checksum(body: bytes) -> bytes:
    """8-bit Fletcher checksum over class, id, length and payload."""
    ck_a = ck_b = 0
    for byte in body:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return bytes((ck_a, ck_b))


def message(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    body = struct.pack("<BBH", msg_class, msg_id, len(payload)) + payload
    return SYNC + body + checksum(body)


def cfg_rate(rate_hz: float) -> bytes:
    """Navigation solution rate. The NEO-6M supports up to 5 Hz."""
    meas_ms = round(1000 / rate_hz)
    return message(CLASS_CFG, ID_CFG_RATE, struct.pack("<HHH", meas_ms, 1, 1))  # navRate 1, GPS time


def cfg_nmea_rate(sentence: str, every_n_epochs: int) -> bytes:
    """Output a sentence every N navigation epochs on the current port (0 = off)."""
    return message(CLASS_CFG, ID_CFG_MSG, bytes((NMEA_CLASS, NMEA_IDS[sentence], every_n_epochs)))


def setup_sequence(rate_hz: float) -> list[bytes]:
    """RMC + GGA only, so 5 Hz fits in 9600 baud.

    Per epoch RMC + GGA are about 150 bytes: 150 B x 5 Hz = 750 B/s, below the
    960 B/s of 9600 baud. The default sentence set (~450 B) would not fit.
    """
    keep = {"RMC", "GGA"}
    msgs = [cfg_nmea_rate(name, 1 if name in keep else 0) for name in NMEA_IDS]
    msgs.append(cfg_rate(rate_hz))
    return msgs
