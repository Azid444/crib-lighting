"""First-run wizard: config building and the full save round trip."""
import os

import pytest
import yaml
from fastapi.testclient import TestClient

from crib.setup import build_config, is_complete, load_config, save_config

WLED = {"driver": "wled", "id": "strip", "name": "Strip", "host": "1.2.3.4",
        "_detail": "ui only"}
TUYA = {"driver": "tuya", "id": "ceiling", "name": "Lights",
        "device_id": "abc", "address": "1.2.3.5", "local_key": "", "version": 3.3}


# --- config building ------------------------------------------------------

def test_ui_only_fields_are_not_written():
    cfg = build_config([WLED])
    assert "_detail" not in cfg["devices"][0]


def test_incomplete_tuya_is_left_out():
    """A switch with no key would only produce connection errors."""
    assert build_config([TUYA])["devices"] == []
    assert build_config([{**TUYA, "local_key": "k"}])["devices"] != []


def test_is_complete_per_driver():
    assert is_complete(WLED)
    assert not is_complete({"driver": "wled", "host": ""})
    assert is_complete({"driver": "mrstar", "address": "AA:BB"})
    assert not is_complete({"driver": "mrstar", "address": ""})
    assert not is_complete({"driver": "nonsense"})


def test_duplicate_ids_are_made_unique():
    cfg = build_config([WLED, {**WLED, "host": "1.2.3.9"}])
    assert [d["id"] for d in cfg["devices"]] == ["strip", "strip2"]


def test_defaults_are_filled_in():
    cfg = build_config([WLED])
    assert cfg["server"]["port"] == 8080
    assert cfg["audio"]["sensitivity"] == 1.35
    assert cfg["source"] == "auto"


def test_existing_settings_survive_a_rescan():
    """Re-running setup must not wipe tuning the user already did."""
    existing = {"audio": {"delay_ms": 180, "sensitivity": 1.8}, "source": "mic"}
    cfg = build_config([WLED], existing=existing)
    assert cfg["audio"]["delay_ms"] == 180 and cfg["source"] == "mic"


def test_spotify_only_written_when_given():
    assert "spotify" not in build_config([WLED])
    cfg = build_config([WLED], spotify_client_id="xyz")
    assert cfg["spotify"]["client_id"] == "xyz"
    assert "callback" in cfg["spotify"]["redirect_uri"]


# --- file handling --------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    path = str(tmp_path / "config.yaml")
    cfg = build_config([WLED])
    save_config(path, cfg)
    assert load_config(path) == cfg


def test_existing_config_is_backed_up(tmp_path):
    path = str(tmp_path / "config.yaml")
    save_config(path, {"devices": [], "marker": 1})
    save_config(path, {"devices": [], "marker": 2})
    assert load_config(path)["marker"] == 2
    assert yaml.safe_load(open(path + ".bak"))["marker"] == 1


def test_missing_config_is_not_an_error(tmp_path):
    assert load_config(str(tmp_path / "nope.yaml")) == {}


def test_unreadable_config_does_not_raise(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("{{{ not yaml")
    assert load_config(str(path)) == {}


def test_no_temp_file_is_left_behind(tmp_path):
    path = str(tmp_path / "config.yaml")
    save_config(path, build_config([WLED]))
    assert not os.path.exists(path + ".tmp")


# --- the wizard over HTTP -------------------------------------------------

@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """An app with no config at all, as on a first run."""
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr("crib.app.CONFIG", str(cfg))
    from crib.app import app
    with TestClient(app) as c:
        yield c, str(cfg)


def test_first_run_redirects_to_the_wizard(fresh):
    client, _ = fresh
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/setup"


def test_wizard_page_is_served(fresh):
    client, _ = fresh
    assert "Scan for my lights" in client.get("/setup").text


def test_state_reports_setup_needed(fresh):
    client, _ = fresh
    s = client.get("/api/setup/state").json()
    assert s["needs_setup"] is True and s["configured"] == []


def test_save_writes_config_and_brings_lights_up(fresh):
    client, path = fresh
    r = client.post("/api/setup/save", json={"devices": [WLED]})
    assert r.status_code == 200 and r.json()["saved"] is True

    written = load_config(path)
    assert written["devices"][0]["host"] == "1.2.3.4"
    assert "_detail" not in written["devices"][0]

    # The running app picked it up without a restart.
    state = client.get("/api/state").json()
    assert [l["id"] for l in state["lights"]] == ["strip"]
    assert client.get("/", follow_redirects=False).status_code == 200


def test_saving_spotify_id_configures_it(fresh):
    client, _ = fresh
    client.post("/api/setup/save",
                json={"devices": [WLED], "spotify_client_id": "cid"})
    assert client.get("/api/state").json()["spotify"]["configured"] is True


def test_saving_nothing_is_allowed(fresh):
    """Skipping every device must not wedge the app."""
    client, _ = fresh
    assert client.post("/api/setup/save", json={"devices": []}).status_code == 200
    assert client.get("/api/setup/state").json()["needs_setup"] is True


# --- startup with no config ----------------------------------------------

def test_main_starts_without_a_config_file(tmp_path, monkeypatch):
    """First run has no config.yaml; main() must still boot the wizard."""
    import crib.app as appmod

    monkeypatch.setattr(appmod, "CONFIG", str(tmp_path / "missing.yaml"))
    called = {}
    monkeypatch.setattr(appmod.uvicorn, "run",
                        lambda app, host, port: called.update(host=host, port=port))
    appmod.main()                      # must not raise
    assert called == {"host": "0.0.0.0", "port": 8080}


def test_main_honours_configured_host_and_port(tmp_path, monkeypatch):
    import crib.app as appmod

    path = tmp_path / "config.yaml"
    save_config(str(path), {"server": {"host": "127.0.0.1", "port": 9000},
                            "devices": []})
    monkeypatch.setattr(appmod, "CONFIG", str(path))
    called = {}
    monkeypatch.setattr(appmod.uvicorn, "run",
                        lambda app, host, port: called.update(host=host, port=port))
    appmod.main()
    assert called == {"host": "127.0.0.1", "port": 9000}


def test_main_survives_a_config_with_no_server_section(tmp_path, monkeypatch):
    import crib.app as appmod

    path = tmp_path / "config.yaml"
    save_config(str(path), {"devices": [], "server": None})
    monkeypatch.setattr(appmod, "CONFIG", str(path))
    called = {}
    monkeypatch.setattr(appmod.uvicorn, "run",
                        lambda app, host, port: called.update(port=port))
    appmod.main()
    assert called["port"] == 8080
