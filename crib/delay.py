"""Holds the light signal back so it lands with the sound you actually hear.

Capture happens before the speakers do their work, so anything downstream of
the capture point adds lag the lights do not have:

    Bluetooth (A2DP)   100-250 ms
    HDMI / eARC         30-120 ms
    Wired line-out        0-10 ms

Without compensation the lights run ahead of the music, which reads as badly
out of sync. This wraps any beat source and republishes its signals a fixed
delay later, so the two line up again.

It only ever delays. Nothing can make a live capture fire early, so a wired
setup simply needs an offset of zero.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque

log = logging.getLogger(__name__)

# Fields republished from the wrapped source.
SIGNALS = ("beats", "spikes", "bass", "mid", "treble", "level")
# How often we sample; finer than this is wasted against a 20fps light loop.
TICK = 0.005


class Delayed:
    """Same interface as Audio and SpotifyBeats, shifted later in time."""

    def __init__(self, inner, delay_s: float = 0.0) -> None:
        self.inner = inner
        self.delay_s = max(0.0, delay_s)
        self.beats = self.spikes = 0
        self.bass = self.mid = self.treble = self.level = 0.0
        self.beat = self.spike = False
        self._history: deque[tuple[float, tuple]] = deque()
        self._task: asyncio.Task | None = None

    # Anything not delayed (bpm, track, error...) reads straight through.
    def __getattr__(self, name):
        return getattr(self.inner, name)

    def sample(self, now: float) -> None:
        """Record the source's current signals and publish the older ones."""
        self._history.append(
            (now, tuple(getattr(self.inner, f, 0) for f in SIGNALS))
        )
        cutoff = now - self.delay_s
        chosen = None
        while self._history and self._history[0][0] <= cutoff:
            chosen = self._history.popleft()[1]
        if chosen is None:
            return
        # Counters are cumulative, so publishing the newest sample at or
        # before the cutoff cannot lose a beat even if several fell in one
        # tick -- the count carries them.
        previous = dict(zip(SIGNALS, chosen))
        self.beat = previous["beats"] > self.beats
        self.spike = previous["spikes"] > self.spikes
        for field, value in previous.items():
            setattr(self, field, value)

    async def start(self) -> None:
        try:
            result = self.inner.start()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            # A source with no device still gets wrapped: the effect falls
            # back to a fixed tempo rather than the whole thing failing.
            log.warning("delayed source failed to start: %s", exc)
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self._history.clear()
        try:
            self.inner.stop()
        except Exception:
            log.warning("delayed source failed to stop", exc_info=True)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                self.sample(loop.time())
                await asyncio.sleep(TICK)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("delay loop crashed")

    def snapshot(self) -> dict:
        snap = dict(self.inner.snapshot())
        snap.update({f: getattr(self, f) for f in SIGNALS})
        snap["delay_ms"] = round(self.delay_s * 1000)
        return snap
