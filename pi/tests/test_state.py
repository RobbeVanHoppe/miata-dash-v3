from miata_dash.state import VehicleState


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_update_and_age():
    clock = FakeClock()
    state = VehicleState(clock)
    state.update("ecu.RPM", 3000, "rpm")
    clock.now += 1.5
    snap = state.snapshot()
    assert snap["ecu.RPM"] == {"value": 3000, "unit": "rpm", "age": 1.5}


def test_value_respects_max_age():
    clock = FakeClock()
    state = VehicleState(clock)
    state.update("gps.speed_kph", 50.0)
    assert state.value("gps.speed_kph", max_age=2) == 50.0
    clock.now += 3
    assert state.value("gps.speed_kph", max_age=2, default=-1) == -1
    assert state.value("missing") is None
