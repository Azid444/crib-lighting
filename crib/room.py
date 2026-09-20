"""Holds the room: the lights, the effect engine, and scene presets."""
from __future__ import annotations

import asyncio
import logging

import yaml

from .audio import Audio
from .drivers import Light, build
from .effects import Engine

log = logging.getLogger(__name__)

# Named one-shot looks. Anything not listed for a scene is turned off.
SCENES: dict[str, dict] = {
    "off": {},
    "bright": {"*": {"on": True, "brightness": 255, "color": [255, 240, 220]}},
    "chill": {"*": {"on": True, "brightness": 90, "color": [255, 120, 30]}},
    "movie": {
        "ceiling": {"on": False},
        "*": {"on": True, "brightness": 60, "color": [60, 80, 255]},
    },
}


class Room:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.lights: dict[str, Light] = {}
        for spec in config.get("devices", []):
            try:
                light = build(spec)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                # Skip a device we cannot even construct (bad config, missing
                # driver dependency) rather than refusing to start. Broader
                # than Exception because native import panics are not one.
                log.error("skipping device %s: %s", spec.get("id", "?"), exc)
                continue
            self.lights[light.id] = light
        audio_cfg = config.get("audio", {}) or {}
        self.audio = Audio(
            device=audio_cfg.get("device"),
            sensitivity=float(audio_cfg.get("sensitivity", 1.35)),
        )
        self.engine = Engine(self.lights, audio=self.audio)

    @classmethod
    def from_file(cls, path: str) -> "Room":
        with open(path) as fh:
            return cls(yaml.safe_load(fh))

    async def connect_all(self) -> None:
        """Connect everything, tolerating devices that are off or asleep."""
        async def one(light: Light) -> None:
            try:
                await light.connect()
                log.info("connected %s (%s)", light.id, light.name)
            except Exception as exc:
                log.warning("could not reach %s: %s", light.id, exc)

        await asyncio.gather(*(one(l) for l in self.lights.values()))

    async def disconnect_all(self) -> None:
        await self.engine.stop()
        self.audio.stop()
        await asyncio.gather(
            *(l.disconnect() for l in self.lights.values()), return_exceptions=True
        )

    async def apply_scene(self, name: str) -> None:
        if name not in SCENES:
            raise ValueError(f"unknown scene {name!r}; have {sorted(SCENES)}")
        await self.engine.stop()
        spec = SCENES[name]
        jobs = []
        for id, light in self.lights.items():
            target = spec.get(id, spec.get("*", {"on": False}))
            jobs.append(light.set(throttle=False, **target))
        await asyncio.gather(*jobs, return_exceptions=True)

    async def all_off(self) -> None:
        await self.apply_scene("off")

    def snapshot(self) -> dict:
        return {
            "lights": [l.snapshot() for l in self.lights.values()],
            "effect": self.engine.running,
            "scenes": sorted(SCENES),
            "audio": self.audio.snapshot(),
        }
