"""Frame-based effects driven from one clock, so every light stays on beat.

An effect returns the target state for each light at time `t`. The engine ticks
at FPS and hands each target to the driver, which throttles it down to whatever
that device can physically keep up with. A device that cannot strobe is simply
never given a strobing target.
"""
from __future__ import annotations

import asyncio
import colorsys
import logging
import math
import random

from .drivers import Light

log = logging.getLogger(__name__)

FPS = 20.0

RGB = tuple[int, int, int]


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (round(r * 255), round(g * 255), round(b * 255))


class Effect:
    name = "effect"
    # Effects that set this get an Audio analyser started for them.
    needs_audio = False

    def __init__(self, **opts) -> None:
        self.opts = opts
        self.audio = None  # set by the engine when needs_audio is True

    def frame(self, t: float, light: Light) -> dict | None:
        """Target state for `light` at `t` seconds in, or None to leave it be."""
        raise NotImplementedError


class Solid(Effect):
    name = "solid"

    def frame(self, t: float, light: Light) -> dict | None:
        color = tuple(self.opts.get("color", (255, 180, 100)))
        bri = int(self.opts.get("brightness", 255))
        return {"on": bri > 0, "brightness": bri, "color": color}


class Rainbow(Effect):
    """Slow hue sweep. Lights are phase-offset so the room drifts, not blinks."""

    name = "rainbow"

    def frame(self, t: float, light: Light) -> dict | None:
        speed = float(self.opts.get("speed", 0.05))
        phase = (hash(light.id) % 100) / 100.0
        if not light.caps.color:
            return {"on": True}
        return {"on": True, "brightness": 255, "color": hsv(t * speed + phase)}


class Breathe(Effect):
    name = "breathe"

    def frame(self, t: float, light: Light) -> dict | None:
        period = float(self.opts.get("period", 6.0))
        color = tuple(self.opts.get("color", (255, 120, 40)))
        # sine mapped to 0..1, floored so it never fully drops out
        level = (math.sin(2 * math.pi * t / period) + 1) / 2
        bri = round(25 + level * 230)
        if not light.caps.brightness:
            return {"on": True}
        return {"on": True, "brightness": bri, "color": color}


class Strobe(Effect):
    name = "strobe"

    def frame(self, t: float, light: Light) -> dict | None:
        hz = float(self.opts.get("hz", 8.0))
        color = tuple(self.opts.get("color", (255, 255, 255)))
        if not light.caps.can_strobe:
            # Relays stay out of it.
            return {"on": False}
        on = (t * hz) % 1.0 < 0.5
        return {"on": on, "brightness": 255, "color": color}


class Rave(Effect):
    """Beat-synced colour slamming with strobe accents.

    Every strobe-capable light shares one beat counter, so the TV backlight and
    the strip hit the same colour on the same beat even though one is on
    Bluetooth and the other on WiFi.
    """

    name = "rave"

    def __init__(self, **opts) -> None:
        super().__init__(**opts)
        self._beat = -1
        self._color: RGB = (255, 0, 0)

    def frame(self, t: float, light: Light) -> dict | None:
        bpm = float(self.opts.get("bpm", 128.0))
        beat_len = 60.0 / bpm
        beat = int(t / beat_len)

        if beat != self._beat:
            self._beat = beat
            # Saturated, well-separated hues — pastels read as mud at speed.
            self._color = hsv(random.choice([0.0, 0.08, 0.33, 0.5, 0.66, 0.83]))

        if not light.caps.can_strobe:
            return {"on": False}

        pos = (t % beat_len) / beat_len  # 0..1 through the current beat
        if beat % 4 == 3:
            # Every fourth beat: hard strobe burst.
            on = (t * 16.0) % 1.0 < 0.5
            return {"on": on, "brightness": 255, "color": (255, 255, 255)}
        # Otherwise: colour slam that decays across the beat.
        bri = round(255 * max(0.15, 1.0 - pos * 1.3))
        return {"on": True, "brightness": bri, "color": self._color}


class SoundRave(Effect):
    """Rave driven by what the room is actually hearing.

    Bass sets brightness, a detected kick slams a new colour, and treble
    spikes flash white — so the lights follow the track instead of a metronome.
    Falls back to the fixed-tempo Rave if no audio is coming in, so the button
    still does something when the loopback device is not set up.
    """

    name = "sound"
    needs_audio = True

    def __init__(self, **opts) -> None:
        super().__init__(**opts)
        self._color: RGB = hsv(0.0)
        self._hue = 0.0
        self._fallback = Rave(**opts)
        self._flash_until = 0.0
        self._seen_beats = 0
        self._seen_spikes = 0

    def frame(self, t: float, light: Light) -> dict | None:
        if not light.caps.can_strobe:
            return {"on": False}
        a = self.audio
        if a is None or not a.running:
            return self._fallback.frame(t, light)

        gain = float(self.opts.get("gain", 1.0))

        # Compare counters rather than reading the one-block `beat` flag, which
        # this loop ticks too slowly to catch reliably.
        beats = getattr(a, "beats", 0)
        if beats != self._seen_beats:
            self._seen_beats = beats
            # Jump a large, irregular step around the wheel so consecutive
            # beats never land on similar colours.
            self._hue = (self._hue + 0.37 + random.random() * 0.2) % 1.0
            self._color = hsv(self._hue)

        # Treble transients (hats, snare) punch a short white flash. The
        # analyser decides what counts as a spike relative to the track's own
        # recent treble, so this works at any volume.
        spikes = getattr(a, "spikes", 0)
        if spikes != self._seen_spikes:
            self._seen_spikes = spikes
            if t > self._flash_until:
                self._flash_until = t + 0.06
        if t < self._flash_until:
            return {"on": True, "brightness": 255, "color": (255, 255, 255)}

        loud = min(1.0, a.bass * 12.0 * gain)
        bri = round(30 + loud * 225)
        return {"on": True, "brightness": bri, "color": self._color}


EFFECTS: dict[str, type[Effect]] = {
    e.name: e for e in (Solid, Rainbow, Breathe, Strobe, Rave, SoundRave)
}


class Engine:
    """Runs at most one effect at a time over a set of lights."""

    def __init__(self, lights: dict[str, Light], audio=None) -> None:
        self.lights = lights
        self.audio = audio
        self.current: Effect | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> str | None:
        return self.current.name if self.current else None

    async def start(self, name: str, **opts) -> None:
        if name not in EFFECTS:
            raise ValueError(f"unknown effect {name!r}; have {sorted(EFFECTS)}")
        await self.stop()
        effect = EFFECTS[name](**opts)
        if effect.needs_audio and self.audio is not None:
            effect.audio = self.audio
            try:
                self.audio.start()
            except Exception as exc:
                # No loopback device? Run anyway; the effect falls back to a
                # fixed tempo rather than refusing to start.
                log.warning("audio unavailable, using fixed tempo: %s", exc)
        self.current = effect
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.current is not None and self.current.needs_audio and self.audio:
            self.audio.stop()
        self.current = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        interval = 1.0 / FPS
        try:
            while True:
                t = loop.time() - t0
                targets = []
                for light in self.lights.values():
                    target = self.current.frame(t, light) if self.current else None
                    if target is not None:
                        targets.append(light.set(**target))
                # Fire every device in parallel: a slow BLE write must not
                # delay the strip's frame.
                if targets:
                    await asyncio.gather(*targets, return_exceptions=True)
                # Sleep to the next frame boundary rather than a flat interval,
                # so the beat does not drift as frames take varying time.
                await asyncio.sleep(max(0.0, (t0 + (int(t / interval) + 1) * interval) - loop.time()))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("effect loop crashed")
