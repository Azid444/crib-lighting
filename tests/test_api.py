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


def test_state_includes_audio(client):
    audio = client.get("/api/state").json()["audio"]
    assert audio["running"] is False and "bpm" in audio


def test_sound_effect_starts_without_a_sound_card(client):
    """The sound button must not 500 on a machine with no loopback."""
    r = client.post("/api/effect", json={"name": "sound"})
    assert r.status_code == 200 and r.json()["effect"] == "sound"
    client.delete("/api/effect")


def test_ui_exposes_sound_controls(client):
    html = client.get("/").text
    assert "SOUND REACTIVE" in html and 'id="meter"' in html


# --- source selection and Spotify ----------------------------------------

def test_state_reports_source_and_spotify(client):
    s = client.get("/api/state").json()
    assert s["source"] == "auto"
    assert s["active_source"] == "mic"          # no Spotify grid configured
    assert s["spotify"]["configured"] is False


def test_source_can_be_switched(client):
    assert client.post("/api/source", json={"source": "mic"}).json()["source"] == "mic"
    s = client.post("/api/source", json={"source": "spotify"}).json()
    assert s["source"] == "spotify" and s["active_source"] == "spotify"


def test_unknown_source_is_rejected(client):
    assert client.post("/api/source", json={"source": "telepathy"}).status_code == 400


def test_switching_source_rebinds_a_running_effect(client):
    client.post("/api/effect", json={"name": "sound"})
    s = client.post("/api/source", json={"source": "spotify"}).json()
    assert s["effect"] == "sound", "effect should survive a source switch"
    client.delete("/api/effect")


def test_spotify_endpoints_fail_clearly_when_unconfigured(client):
    assert client.get("/api/spotify/login").status_code == 400
    assert client.post("/api/spotify/play").status_code == 400


def test_spotify_callback_reports_denial(client):
    r = client.get("/api/spotify/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and "declined" in r.text


def test_settings_are_tweakable_from_the_phone(client):
    s = client.post("/api/settings", json={"sensitivity": 1.8, "bpm": 174}).json()
    assert s["settings"]["sensitivity"] == 1.8 and s["settings"]["bpm"] == 174


def test_settings_are_validated(client):
    assert client.post("/api/settings", json={"sensitivity": 99}).status_code == 422
    assert client.post("/api/settings", json={"bpm": 5}).status_code == 422


def test_sensitivity_reaches_the_detector(client):
    from crib import app as appmod
    client.post("/api/settings", json={"sensitivity": 2.0})
    assert appmod.room.audio.sensitivity == 2.0


def test_ui_has_spotify_and_settings_controls(client):
    html = client.get("/").text
    for part in ('id="np"', 'data-src="spotify"', 'id="sens"', 'id="playpause"'):
        assert part in html


def test_sync_delay_is_settable(client):
    s = client.post("/api/settings", json={"delay_ms": 220}).json()
    assert s["settings"]["delay_ms"] == 220


def test_sync_delay_is_bounded(client):
    assert client.post("/api/settings", json={"delay_ms": -5}).status_code == 422
    assert client.post("/api/settings", json={"delay_ms": 5000}).status_code == 422


def test_delay_wraps_a_running_effect(client):
    from crib import app as appmod
    client.post("/api/effect", json={"name": "sound"})
    client.post("/api/settings", json={"delay_ms": 200})
    assert appmod.room._delayed is not None
    assert appmod.room.engine.audio is appmod.room._delayed
    # Back to zero unwraps again.
    client.post("/api/settings", json={"delay_ms": 0})
    assert appmod.room._delayed is None
    client.delete("/api/effect")


def test_ui_has_the_delay_slider(client):
    assert 'id="del"' in client.get("/").text
