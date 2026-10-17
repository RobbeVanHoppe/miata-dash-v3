import pytest

from miata_dash.radio import BANDS_BY_ID, RadioController, find_stations
from miata_dash.simulator import FakeRadioRunner


@pytest.fixture
def radio():
    return RadioController(FakeRadioRunner(), tools_present=True)


def test_fm_command(radio):
    radio.tune("fm", 101.3)
    producer, consumer = radio.runner.started[-1]
    assert producer[:6] == ["rtl_fm", "-M", "wbfm", "-f", "101.30000M", "-s"]
    assert producer[-1] == "-"
    assert "-l" not in producer  # no squelch on broadcast FM
    assert consumer[:2] == ["aplay", "-q"] and consumer[-2:] == ["-D", "default"]


def test_airband_uses_am_and_squelch(radio):
    radio.tune("air")
    radio.set_squelch(120)
    producer, _ = radio.runner.started[-1]
    assert producer[2] == "am"
    assert producer[producer.index("-l") + 1] == "120"


def test_snap_and_range(radio):
    radio.tune("fm", 99.97)
    assert radio.freq_mhz == 100.0
    with pytest.raises(ValueError):
        radio.tune("fm", 120.0)
    with pytest.raises(ValueError):
        radio.tune("police")


def test_step_wraps_at_band_edges(radio):
    radio.tune("fm", 108.0)
    radio.step(1)
    assert radio.freq_mhz == 87.5
    radio.step(-1)
    assert radio.freq_mhz == 108.0
    radio.tune("pmr", 446.00625)
    radio.step(1)
    assert radio.freq_mhz == pytest.approx(446.01875)


def test_restart_stops_previous_pipeline(radio):
    radio.tune("fm", 100.0)
    radio.tune("fm", 101.0)
    assert radio.status()["playing"]
    radio.stop()
    assert not radio.status()["playing"]


def test_dead_pipeline_reports_error(radio):
    radio.tune("fm", 100.0)
    radio._pipeline.stopped = True  # rtl_fm exited, e.g. dongle unplugged
    status = radio.status()
    assert not status["playing"] and "RTL-SDR" in status["error"]


def test_scan_finds_stations_and_resumes(radio):
    radio.tune("fm", 100.0)
    assert set(radio.scan_fm()) == set(FakeRadioRunner.STATIONS_MHZ)
    assert radio.status()["playing"]


def test_find_stations_merges_close_peaks():
    row = "d, t, 100000000, 101000000, 50000, 1, " + ", ".join(
        ["-50"] * 5 + ["-30", "-29", "-30"] + ["-50"] * 6 + ["-35"] + ["-50"] * 6
    )
    assert find_stations(row) == [100.3, 100.7]


def test_no_police_band():
    assert all("police" not in b.lower() for b in BANDS_BY_ID)
