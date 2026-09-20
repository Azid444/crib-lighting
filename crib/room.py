"""Holds the room: the lights, the effect engine, and scene presets."""
from __future__ import annotations

import asyncio
import logging

from .audio import Audio
from .delay import Delayed
from .spotify import SpotifyBeats, SpotifyClient
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
            loopback=bool(audio_cfg.get("loopback", True)),
        )
        self.engine = Engine(self.lights, audio=self.audio)

        # Spotify is optional and independent of the lights: it supplies
        # now-playing metadata (and a beat grid, where the account still has
        # access to it) for whatever device is actually playing.
        sp = config.get("spotify", {}) or {}
        self.spotify: SpotifyClient | None = None
        if sp.get("client_id"):
            self.spotify = SpotifyClient(
                sp["client_id"],
                redirect_uri=sp.get(
                    "redirect_uri",
                    "http://127.0.0.1:8080/api/spotify/callback",
                ),
                token_path=sp.get("token_path", "spotify_token.json"),
            )
        self.spotify_beats = SpotifyBeats(self.spotify)
        # auto | mic | spotify
        self.source_pref = str(config.get("source", "auto"))
        # Live-tweakable from the phone; effects read these at start.
        self.settings = {
            "sensitivity": float(audio_cfg.get("sensitivity", 1.35)),
            "bpm": float(config.get("bpm", 128.0)),
            "gain": float(audio_cfg.get("gain", 1.0)),
            # Bluetooth speakers lag 100-250ms behind the loopback tap.
            "delay_ms": float(audio_cfg.get("delay_ms", 0)),
        }
        self._delayed: Delayed | None = None

    @classmethod
    def from_file(cls, path: str) -> "Room":
        # A missing config is normal on first run: the setup wizard writes it.
        from .setup import load_config

        return cls(load_config(path))

    @property
    def needs_setup(self) -> bool:
        return not self.lights

    # -- beat sources ------------------------------------------------------
    def pick_source(self):
        """Which signal drives the sound effect.

        Spotify only wins when it actually has a beat grid; with the analysis
        endpoint deprecated for newer apps that is often not the case, so
        `auto` quietly falls back to whatever the sound card hears.
        """
        if self.source_pref == "mic":
            return self.audio
        if self.source_pref == "spotify":
            return self.spotify_beats
        if self.spotify_beats.running and self.spotify_beats.has_grid:
            return self.spotify_beats
        return self.audio

    async def start_effect(self, name: str, **opts) -> None:
        """Start an effect, bringing up whichever beat source it needs."""
        effect_cls = None
        from .effects import EFFECTS
        effect_cls = EFFECTS.get(name)
        # Phone-set defaults, unless the caller overrode them explicitly.
        opts.setdefault("bpm", self.settings["bpm"])
        opts.setdefault("gain", self.settings["gain"])
        if effect_cls is not None and effect_cls.needs_audio:
            source = self.pick_source()
            if source is self.audio:
                try:
                    self.audio.start()
                except Exception as exc:
                    # No input device is not fatal: the effect falls back to a
                    # fixed tempo rather than refusing to start.
                    log.warning("audio unavailable, using fixed tempo: %s", exc)
            self.engine.audio = await self._with_delay(source)
        await self.engine.start(name, **opts)

    async def _with_delay(self, source):
        """Wrap a source so the lights land with the sound you hear."""
        delay = self.settings["delay_ms"] / 1000.0
        if delay <= 0:
            if self._delayed is not None:
                self._delayed.stop()
                self._delayed = None
            return source
        # Reuse the wrapper when nothing changed, so counters do not reset.
        if (self._delayed is None
                or self._delayed.inner is not source
                or self._delayed.delay_s != delay):
            if self._delayed is not None:
                self._delayed.stop()
            self._delayed = Delayed(source, delay)
        await self._delayed.start()
        return self._delayed

    async def stop_effect(self) -> None:
        await self.engine.stop()
        if self._delayed is not None:
            self._delayed.stop()
            self._delayed = None
        self.audio.stop()

    async def start_spotify(self) -> None:
        """Begin polling now-playing. Safe to call repeatedly."""
        if self.spotify and self.spotify.authorized:
            await self.spotify_beats.start()

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
        if self._delayed is not None:
            self._delayed.stop()
            self._delayed = None
        self.audio.stop()
        self.spotify_beats.stop()
        if self.spotify:
            await self.spotify.close()
        await asyncio.gather(
            *(l.disconnect() for l in self.lights.values()), return_exceptions=True
        )

    def update_settings(self, **values) -> None:
        for key, value in values.items():
            if key in self.settings and value is not None:
                self.settings[key] = float(value)
        # Sensitivity is read by the detector on every block, so it takes
        # effect immediately without restarting the effect.
        self.audio.sensitivity = self.settings["sensitivity"]
        # Changing the delay needs the wrapper rebuilt, which start_effect
        # does; apply it live if an effect is already running.
        if self._delayed is not None:
            self._delayed.delay_s = max(0.0, self.settings["delay_ms"] / 1000.0)

    def set_source(self, name: str) -> None:
        if name not in ("auto", "mic", "spotify"):
            raise ValueError(f"unknown source {name!r}")
        self.source_pref = name

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
            "spotify": {
                "configured": self.spotify is not None,
                "authorized": bool(self.spotify and self.spotify.authorized),
                "analysis_allowed": self.spotify.analysis_allowed if self.spotify else None,
                **self.spotify_beats.snapshot(),
            },
            "source": self.source_pref,
            "settings": self.settings,
            "active_source": (
                "spotify" if self.pick_source() is self.spotify_beats else "mic"
            ),
        }
