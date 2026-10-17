import time
from dataclasses import replace

import can
import pytest

from miata_dash.app import NET_DBC, AppConfig, Application
from miata_dash.knob import FINE_TUNING, KnobController
from miata_dash.node_link import PiNodeLink, RelayRequests
from miata_dash.radio import RadioController
from miata_dash.settings import SettingsStore
from miata_dash.simulator import FakeRadioRunner
from miata_dash.sources import CanSource
from miata_dash.state import VehicleState
from miata_dash.ui import UiState


def wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


# -- CanSource with two DBCs ---------------------------------------------------
def test_second_dbc_decodes_with_its_prefix_and_calls_listeners(dbc_path):
    state = VehicleState()
    source = CanSource(state, dbc_path, "virtual", "x", extra_dbcs=((NET_DBC, "net."),))
    got = []
    source.add_listener("KNOB_STATE", got.append)
    m = source.message("KNOB_STATE")
    data = m.encode({"Knob_Position": -3, "Knob_Delta": 2, "Knob_Pressed": 1, "Knob_LongPress": 0,
                     "Knob_MotorOff": 0, "Knob_Angle": 12.3, "Knob_Counter": 9})
    source.handle(can.Message(arbitration_id=m.frame_id, data=data, is_extended_id=False))
    assert state.value("net.Knob_Position") == -3
    assert got and got[0]["Knob_Delta"] == 2
    # ME221 frames still land under ecu.
    me = source.message("ME1_1")
    source.handle(can.Message(arbitration_id=me.frame_id, is_extended_id=False,
                              data=me.encode({"RPM": 900, "TPS": 0, "MAP": 30, "IAT": 20})))
    assert state.value("ecu.RPM") == 900


# -- PiNodeLink ------------------------------------------------------------------
class CaptureCan:
    def __init__(self, dbc_path):
        self.inner = CanSource(VehicleState(), dbc_path, "virtual", "x", extra_dbcs=((NET_DBC, "net."),))
        self.sent = []

    def send_named(self, name, values):
        self.inner.message(name).encode(values)  # must be encodable
        self.sent.append((name, values))
        return True


def test_node_link_frames(tmp_path, dbc_path):
    store = SettingsStore(tmp_path / "s.json")
    relays = RelayRequests()
    relays.set("O03", True)
    can_ = CaptureCan(dbc_path)
    link = PiNodeLink(VehicleState(), can_, store, relays, lambda: {"Prof_Mode": 1, "Prof_DetentsPerRev": 12,
                                                                   "Prof_Strength": 70, "Prof_MinPos": 0,
                                                                   "Prof_MaxPos": 0})
    for _ in range(5):
        link.tick()
    names = [n for n, _ in can_.sent]
    assert names.count("PI_MAIN_CMD") == 5
    assert names.count("PI_STATUS") == 1
    assert names.count("PI_KNOB_PROFILE") == 2  # first tick (new profile) + tick 5
    cmd = [v for n, v in can_.sent if n == "PI_MAIN_CMD"][-1]
    assert cmd["Pi_RelayReq_O03"] == 1 and cmd["Pi_RelayReq_O02"] == 0
    assert cmd["Pi_AutoLightsOff_En"] == 1 and cmd["Pi_Counter"] == 5


def test_unknown_relay_rejected():
    with pytest.raises(ValueError):
        RelayRequests().set("O01", True)  # O01 (lights-off latch) belongs to the main node


# -- knob ------------------------------------------------------------------------
@pytest.fixture
def knob(tmp_path):
    ui = UiState()
    store = SettingsStore(tmp_path / "s.json")
    radio = RadioController(FakeRadioRunner(), tools_present=True)
    return KnobController(ui, store, radio), ui, store, radio


def frame(delta=0, pressed=0, long_press=0):
    return {"Knob_Delta": delta, "Knob_Pressed": pressed, "Knob_LongPress": long_press}


def test_long_press_goes_to_next_page_without_short_press(knob):
    k, ui, store, _ = knob
    k.on_frame(frame(pressed=1))
    k.on_frame(frame(pressed=1, long_press=1))
    k.on_frame(frame(pressed=0))
    assert ui.current == "/radio"
    assert store.settings.layout == "digital"  # the release did not also cycle the layout


def test_dash_turn_and_press_cycle_layouts(knob):
    k, ui, store, _ = knob
    seq = ui.reload_seq
    k.on_frame(frame(delta=1))
    assert store.settings.layout == "retro" and ui.reload_seq == seq + 1
    k.on_frame(frame(delta=-1))
    assert store.settings.layout == "digital"
    k.on_frame(frame(pressed=1))
    k.on_frame(frame(pressed=0))
    assert store.settings.layout == "retro"


def test_radio_turn_tunes_and_press_toggles(knob):
    k, ui, _, radio = knob
    ui.report("/radio")
    radio.tune("fm", 100.0)
    k.on_frame(frame(delta=3))
    assert radio.freq_mhz == pytest.approx(100.3)
    k.on_frame(frame(pressed=1))
    k.on_frame(frame(pressed=0))
    assert not radio.status()["playing"]
    assert k.profile()["Prof_Mode"] == FINE_TUNING


def test_absurd_delta_ignored(knob):
    k, ui, store, _ = knob
    k.on_frame(frame(delta=100))
    assert store.settings.layout == "digital"


# -- whole app in simulation -------------------------------------------------------
def test_relay_request_round_trip_through_fake_main_node(tmp_path):
    app = Application(AppConfig(simulate=True, settings_path=tmp_path / "s.json", log_dir=tmp_path / "logs"))
    app.start()
    try:
        assert wait_for(lambda: app.state.value("net.Main_IG1") == 1)
        app.relays.set("O02", True)
        assert wait_for(lambda: app.state.value("net.Main_Relay_O02") == 1)
        app.relays.set("O02", False)
        assert wait_for(lambda: app.state.value("net.Main_Relay_O02") == 0)
    finally:
        app.stop()
