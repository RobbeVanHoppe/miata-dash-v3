from dataclasses import replace

import pytest

from miata_dash.settings import SettingsStore
from miata_dash.sources import CanSource, EcuSpeedSender
from miata_dash.state import VehicleState


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path, dbc_path):
    clock = FakeClock()
    state = VehicleState(clock)
    store = SettingsStore(tmp_path / "settings.json")
    store.save(replace(store.settings, send_gps_speed_to_ecu=True))
    can_source = CanSource(state, dbc_path, interface="virtual", channel="unused")
    return clock, state, store, EcuSpeedSender(state, can_source, store)


def decode(sender, frame):
    assert frame.arbitration_id == 0x340
    return sender.message.decode(frame.data)["Vehicle_Speed"]


def test_sends_fresh_gps_speed(setup):
    clock, state, _, sender = setup
    state.update("gps.fix", True)
    state.update("gps.speed_kph", 87.3)
    assert decode(sender, sender.build_frame()) == pytest.approx(87.3, abs=0.008)


def test_silent_when_disabled(setup):
    _, state, store, sender = setup
    store.save(replace(store.settings, send_gps_speed_to_ecu=False))
    state.update("gps.fix", True)
    state.update("gps.speed_kph", 50.0)
    assert sender.build_frame() is None


def test_silent_without_fix_or_when_stale(setup):
    clock, state, _, sender = setup
    state.update("gps.speed_kph", 50.0)
    assert sender.build_frame() is None  # never had a fix
    state.update("gps.fix", False)
    assert sender.build_frame() is None  # lost fix (tunnel)
    state.update("gps.fix", True)
    clock.now += 2.0
    assert sender.build_frame() is None  # data too old


def test_speed_is_clamped(setup):
    _, state, _, sender = setup
    state.update("gps.fix", True)
    state.update("gps.speed_kph", -1.0)
    assert decode(sender, sender.build_frame()) == 0
