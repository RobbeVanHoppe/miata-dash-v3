import time

import can
import pytest

from miata_dash.simulator import FakeEcu, FakeSerialGps
from miata_dash.sources import CanSource, GpsSource
from miata_dash.state import VehicleState


def wait_for(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_can_decodes_me1_1(dbc_path):
    state = VehicleState()
    source = CanSource(state, dbc_path, interface="virtual", channel="unused")
    msg_def = source.db.get_message_by_name("ME1_1")
    data = msg_def.encode({"RPM": 3250, "TPS": 42.5, "MAP": 101.3, "IAT": -5.0})
    source.handle(can.Message(arbitration_id=0x300, data=data, is_extended_id=False))

    assert state.value("ecu.RPM") == 3250
    assert state.value("ecu.TPS") == pytest.approx(42.5)
    assert state.value("ecu.MAP") == pytest.approx(101.3)
    assert state.value("ecu.IAT") == pytest.approx(-5.0)
    assert state.get("ecu.IAT").unit == "deg C"


def test_can_ignores_unknown_and_short_frames(dbc_path):
    state = VehicleState()
    source = CanSource(state, dbc_path, interface="virtual", channel="unused")
    source.handle(can.Message(arbitration_id=0x123, data=bytes(8), is_extended_id=False))
    source.handle(can.Message(arbitration_id=0x300, data=bytes(2), is_extended_id=False))
    assert state.snapshot() == {}


def test_can_source_thread_with_fake_ecu(dbc_path):
    state = VehicleState()
    source = CanSource(state, dbc_path, interface="virtual", channel="test-bus")
    ecu = FakeEcu(dbc_path, "test-bus", period=0.01)
    source.start()
    ecu.start()
    try:
        assert wait_for(lambda: state.value("ecu.RPM") is not None)
        assert wait_for(lambda: state.value("ecu.AFRCurr_1") is not None)
    finally:
        ecu.stop()
        source.stop()
    assert not source.running


def test_gps_source_with_fake_serial():
    state = VehicleState()
    source = GpsSource(state, serial_factory=FakeSerialGps)
    source.start()
    try:
        assert wait_for(lambda: state.value("gps.speed_kph") is not None)
        assert state.value("gps.fix") is True
    finally:
        source.stop()


def test_source_reopens_after_error():
    opens = []

    class Flaky(FakeSerialGps):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            opens.append(self)
            if len(opens) == 1:
                raise OSError("port not ready")

    source = GpsSource(VehicleState(), serial_factory=Flaky)
    source.retry_delay = 0.01
    source.start()
    try:
        assert wait_for(lambda: len(opens) >= 2)
    finally:
        source.stop()
