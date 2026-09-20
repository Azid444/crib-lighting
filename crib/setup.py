"""Builds config.yaml from discovered devices, so nothing is edited by hand."""
from __future__ import annotations

import logging
import os
import shutil

import yaml

log = logging.getLogger(__name__)

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 8080},
    "source": "auto",
    "audio": {"device": None, "loopback": True, "sensitivity": 1.35,
              "delay_ms": 0},
}


def is_complete(device: dict) -> bool:
    """Whether a discovered device has everything it needs to connect.

    A Tuya switch is found by broadcast but its local key never is, so it
    stays incomplete until the user supplies one.
    """
    if device.get("driver") == "tuya":
        return bool(device.get("local_key"))
    if device.get("driver") == "wled":
        return bool(device.get("host"))
    if device.get("driver") == "mrstar":
        return bool(device.get("address"))
    return False


def clean(device: dict) -> dict:
    """Strip the UI-only fields before writing to disk."""
    return {k: v for k, v in device.items() if not k.startswith("_")}


def build_config(devices: list[dict], spotify_client_id: str = "",
                 existing: dict | None = None) -> dict:
    """Merge discovered devices into a config, keeping any existing settings."""
    config = {**DEFAULTS, **(existing or {})}
    # Unique ids, so two strips do not collide.
    out, seen = [], {}
    for device in devices:
        if not is_complete(device):
            continue
        base = device.get("id") or device.get("driver", "light")
        seen[base] = seen.get(base, 0) + 1
        device = clean(device)
        if seen[base] > 1:
            device["id"] = f"{base}{seen[base]}"
        out.append(device)
    config["devices"] = out

    if spotify_client_id:
        config["spotify"] = {
            "client_id": spotify_client_id,
            "redirect_uri": "http://127.0.0.1:8080/api/spotify/callback",
            "token_path": "spotify_token.json",
        }
    return config


def save_config(path: str, config: dict) -> None:
    """Write config.yaml, keeping one backup of whatever was there before."""
    if os.path.exists(path):
        try:
            shutil.copyfile(path, path + ".bak")
        except Exception as exc:
            log.warning("could not back up %s: %s", path, exc)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, default_flow_style=False)
    os.replace(tmp, path)   # atomic: never leaves a half-written config


def load_config(path: str) -> dict:
    try:
        with open(path) as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        log.error("could not read %s: %s", path, exc)
        return {}
