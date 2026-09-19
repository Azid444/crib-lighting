"""Exercises the real ASGI app the phone talks to."""
import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def client(config, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump(config))
    monkeypatch.setattr("crib.app.CONFIG", str(cfg))
    from crib.app import app

    with TestClient(app) as c:
        yield c


def test_state_lists_devices_and_caps(client):
    s = client.get("/api/state").json()
    assert {l["id"] for l in s["lights"]} == {"strip", "ceiling"}
    caps = {l["id"]: l["caps"] for l in s["lights"]}
    assert caps["strip"]["can_strobe"] is True
    assert caps["ceiling"]["can_strobe"] is False
    assert s["effect"] is None


def test_set_light(client):
    s = client.post("/api/light/strip", json={"on": True, "color": [10, 20, 30]}).json()
    strip = next(l for l in s["lights"] if l["id"] == "strip")
    assert strip["on"] and strip["color"] == [10, 20, 30]


def test_unknown_light_is_404(client):
    assert client.post("/api/light/nope", json={"on": True}).status_code == 404


def test_brightness_is_validated(client):
    assert client.post("/api/light/strip", json={"brightness": 999}).status_code == 422


def test_scene_roundtrip(client):
    s = client.post("/api/scene/chill").json()
    strip = next(l for l in s["lights"] if l["id"] == "strip")
    assert strip["on"] and strip["brightness"] == 90
    s = client.post("/api/scene/off").json()
    assert all(not l["on"] for l in s["lights"])


def test_unknown_scene_is_404(client):
    assert client.post("/api/scene/nope").status_code == 404


def test_effect_start_and_stop(client):
    assert client.post("/api/effect", json={"name": "rave", "bpm": 140}).json()["effect"] == "rave"
    assert client.delete("/api/effect").json()["effect"] is None


def test_unknown_effect_is_404(client):
    assert client.post("/api/effect", json={"name": "nope"}).status_code == 404


def test_manual_change_cancels_running_effect(client):
    client.post("/api/effect", json={"name": "rave"})
    s = client.post("/api/light/strip", json={"on": False}).json()
    assert s["effect"] is None, "touching a light should hand control back"


def test_scene_cancels_running_effect(client):
    client.post("/api/effect", json={"name": "rave"})
    assert client.post("/api/scene/chill").json()["effect"] is None


def test_ui_and_manifest_are_served(client):
    assert "RAVE" in client.get("/").text
    assert client.get("/static/manifest.json").json()["short_name"] == "Crib"


def test_websocket_pushes_state(client):
    with client.websocket_connect("/ws") as ws:
        assert "lights" in ws.receive_json()
