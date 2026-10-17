import csv
from dataclasses import replace

import pytest

from miata_dash.logger import DriveLogger
from miata_dash.settings import SettingsStore
from miata_dash.state import VehicleState


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path):
    clock = FakeClock()
    state = VehicleState(clock)
    store = SettingsStore(tmp_path / "settings.json")
    logger = DriveLogger(
        state, store, tmp_path / "logs", ["ecu.RPM", "gps.speed_kph"], end_after_s=60, clock=clock
    )
    logger.open()
    return clock, state, store, logger


def step(clock, state, logger, seconds, rpm=None):
    """Advance time in 1 s steps, updating RPM each step if given."""
    for _ in range(seconds):
        clock.now += 1
        if rpm is not None:
            state.update("ecu.RPM", rpm)
        logger.sample()


def read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_no_file_until_engine_runs(setup):
    clock, state, _, logger = setup
    step(clock, state, logger, 5, rpm=0)
    assert not logger.active
    assert list(logger.log_dir.glob("*.csv")) == []


def test_drive_written_and_ended(setup):
    clock, state, _, logger = setup
    state.update("gps.speed_kph", 42.0)
    step(clock, state, logger, 3, rpm=900)
    path = logger.path
    assert path.name.startswith("0001_")
    step(clock, state, logger, 30, rpm=0)  # short stop: same drive
    assert logger.active
    step(clock, state, logger, 31, rpm=0)
    assert not logger.active

    rows = read(path)
    assert rows[0] == ["t_s", "ecu.RPM", "gps.speed_kph"]
    assert rows[1] == ["0.00", "900", "42.0"]
    assert rows[-1][2] == ""  # GPS value went stale, written empty


def test_next_drive_gets_next_number(setup):
    clock, state, _, logger = setup
    step(clock, state, logger, 2, rpm=900)
    step(clock, state, logger, 62, rpm=0)
    step(clock, state, logger, 2, rpm=900)
    assert logger.path.name.startswith("0002_")


def test_disabled_closes_and_stops(setup):
    clock, state, store, logger = setup
    step(clock, state, logger, 2, rpm=900)
    store.save(replace(store.settings, logging_enabled=False))
    step(clock, state, logger, 2, rpm=900)
    assert not logger.active
    assert len(list(logger.log_dir.glob("*.csv"))) == 1
