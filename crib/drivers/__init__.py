"""Driver registry.

Drivers are imported lazily so that a missing optional dependency only breaks
the device that needs it: a WLED-only setup never has to install bleak, and a
broken BLE stack cannot stop the rest of the room from coming up.
"""
from __future__ import annotations

import importlib

from .base import Caps, Light, State

DRIVERS: dict[str, tuple[str, str]] = {
    "wled": (".wled", "WledLight"),
    "mrstar": (".mrstar", "MrStarLight"),
    "tuya": (".tuya", "TuyaSwitch"),
}


def load(driver: str) -> type[Light]:
    if driver not in DRIVERS:
        raise ValueError(f"unknown driver {driver!r}; have {sorted(DRIVERS)}")
    module, cls = DRIVERS[driver]
    try:
        return getattr(importlib.import_module(module, __package__), cls)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        # Deliberately broader than ImportError: a broken native wheel can
        # raise anything at import time, and pyo3 panics are BaseException.
        # One bad dependency must not sink the whole room.
        raise RuntimeError(
            f"driver {driver!r} failed to load ({type(exc).__name__}: {exc})"
        ) from exc


def build(spec: dict) -> Light:
    spec = dict(spec)
    return load(spec.pop("driver"))(**spec)


__all__ = ["Caps", "Light", "State", "DRIVERS", "load", "build"]
