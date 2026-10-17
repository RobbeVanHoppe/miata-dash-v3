import json

import pytest

from miata_dash.settings import Settings, SettingsStore
from miata_dash.state import VehicleState
from miata_dash.web import create_app


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def client(store):
    state = VehicleState()
    state.update("ecu.RPM", 900, "")
    app = create_app(state, store)
    app.testing = True
    return app.test_client()


def test_afr_hidden_by_default(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="g-rpm"' in html
    assert 'id="g-afr"' not in html


def test_settings_toggle_afr_persists(client, store):
    shown = ["rpm", "speed", "afr"]
    resp = client.post("/settings", data={"shown": shown})
    assert resp.status_code == 302
    assert 'id="g-afr"' in client.get("/").get_data(as_text=True)
    assert 'id="g-clt"' not in client.get("/").get_data(as_text=True)

    saved = json.loads(store.path.read_text(encoding="utf-8"))
    assert "afr" not in saved["hidden_gauges"]
    assert SettingsStore(store.path).settings.gauge_visible("afr")


def test_settings_toggle_ecu_speed(client, store):
    assert store.settings.send_gps_speed_to_ecu is False
    client.post("/settings", data={"shown": ["rpm"], "send_gps_speed_to_ecu": "on"})
    assert store.settings.send_gps_speed_to_ecu is True
    client.post("/settings", data={"shown": ["rpm"]})
    assert store.settings.send_gps_speed_to_ecu is False


def test_api_state(client):
    data = client.get("/api/state").get_json()
    assert data["ecu.RPM"]["value"] == 900


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert SettingsStore(path).settings == Settings()


def test_unknown_settings_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hidden_gauges": [], "old_key": 1}), encoding="utf-8")
    assert SettingsStore(path).settings.hidden_gauges == []


# -- layouts, radio, logs ----------------------------------------------------
from miata_dash.radio import RadioController
from miata_dash.simulator import FakeRadioRunner
from miata_dash.web import create_app as _create_app


@pytest.fixture
def full(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "0001_20261017-210000.csv").write_text("t_s\n0.00\n", encoding="utf-8")
    (logs / "notes.txt").write_text("x", encoding="utf-8")
    radio = RadioController(FakeRadioRunner(), tools_present=True)
    app = _create_app(VehicleState(), store, radio=radio, log_dir=logs)
    app.testing = True
    return app.test_client(), store


@pytest.mark.parametrize("layout", ["digital", "retro", "motorsport"])
def test_every_layout_renders(full, layout):
    client, store = full
    store.save(replace_settings(store, layout=layout))
    html = client.get("/").get_data(as_text=True)
    assert f'data-layout="{layout}"' in html
    assert 'id="g-rpm"' in html


def replace_settings(store, **kw):
    from dataclasses import replace

    return replace(store.settings, **kw)


def test_layout_cycle(full):
    client, store = full
    assert client.post("/api/layout", json={"step": 1}).get_json()["layout"] == "retro"
    assert client.post("/api/layout", json={"step": 1}).get_json()["layout"] == "motorsport"
    assert client.post("/api/layout", json={"step": 1}).get_json()["layout"] == "digital"
    assert client.post("/api/layout", json={"layout": "retro"}).get_json()["layout"] == "retro"
    assert store.settings.layout == "retro"


def test_radio_api_and_presets(full):
    client, store = full
    r = client.post("/api/radio", json={"action": "tune", "band": "fm", "freq_mhz": 94.2}).get_json()
    assert r["playing"] and r["freq_mhz"] == 94.2
    client.post("/api/radio", json={"action": "save_preset"})
    assert store.settings.radio_presets == [{"band": "fm", "freq_mhz": 94.2}]
    assert client.post("/api/radio", json={"action": "tune", "band": "fm", "freq_mhz": 300}).status_code == 400
    client.post("/api/radio", json={"action": "delete_preset", "band": "fm", "freq_mhz": 94.2})
    assert store.settings.radio_presets == []
    assert client.post("/api/radio", json={"action": "scan"}).get_json()["stations"]
    assert client.get("/radio").status_code == 200


def test_logs_list_and_download(full):
    client, _ = full
    html = client.get("/logs").get_data(as_text=True)
    assert "0001_20261017-210000.csv" in html and "notes.txt" not in html
    assert client.get("/logs/0001_20261017-210000.csv").status_code == 200
    assert client.get("/logs/notes.txt").status_code == 404
    assert client.get("/logs/..%2Fsettings.json").status_code == 404


def test_settings_saves_new_fields(full):
    client, store = full
    client.post("/settings", data={
        "shown": ["rpm"], "layout": "motorsport", "shift_rpm": "6800",
        "imu_forward_axis": "-y", "imu_up_axis": "+x", "gps_rate_hz": "2",
    })
    s = store.settings
    assert (s.layout, s.shift_rpm, s.imu_forward_axis, s.imu_up_axis, s.gps_rate_hz) == (
        "motorsport", 6800, "-y", "+x", 2.0)


def test_system_page(full):
    client, _ = full
    html = client.get("/system").get_data(as_text=True)
    assert "net.Main_LightsOffLatched" in html and "Faults" in html
