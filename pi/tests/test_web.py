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
