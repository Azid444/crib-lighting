"""Common interface every light driver implements."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Caps:
    """What a device is physically capable of.

    The effect engine reads these instead of special-casing driver names, so a
    relay-based switch never gets asked to strobe.
    """

    color: bool = False
    brightness: bool = False
    # Safe sustained command rate. A mechanical relay gets a tiny number here;
    # WLED over UDP can take a hundred.
    max_hz: float = 1.0

    @property
    def can_strobe(self) -> bool:
        return self.max_hz >= 5.0


@dataclass
class State:
    on: bool = False
    brightness: int = 255  # 0-255
    color: RGB = (255, 255, 255)


class Light:
    """Base class. Drivers override the _apply* hooks, not the public methods."""

    caps = Caps()

    def __init__(self, id: str, name: str) -> None:
        self.id = id
        self.name = name
        self.state = State()
        self.available = False
        self._lock = asyncio.Lock()
        self._min_interval = 1.0 / self.caps.max_hz
        self._last_send = 0.0

    # -- lifecycle ---------------------------------------------------------
    async def connect(self) -> None:
        self.available = True

    async def disconnect(self) -> None:
        self.available = False

    # -- driver hooks ------------------------------------------------------
    async def _apply(self, state: State) -> None:
        raise NotImplementedError

    # -- public API --------------------------------------------------------
    async def set(
        self,
        *,
        on: bool | None = None,
        brightness: int | None = None,
        color: RGB | None = None,
        throttle: bool = True,
    ) -> None:
        """Apply a partial state change, respecting the device's rate limit.

        Frames dropped by the throttle are intentional: during an effect we
        would rather skip a frame than queue up a backlog the device can never
        drain.
        """
        new = replace(self.state)
        if on is not None:
            new.on = on
        if brightness is not None:
            new.brightness = max(0, min(255, brightness))
        if color is not None:
            new.color = color

        loop = asyncio.get_running_loop()
        if throttle and loop.time() - self._last_send < self._min_interval:
            return

        async with self._lock:
            self._last_send = loop.time()
            try:
                await self._apply(new)
                self.state = new
                self.available = True
            except Exception as exc:  # a dropped frame must not kill the loop
                self.available = False
                log.warning("%s: apply failed: %s", self.id, exc)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "available": self.available,
            "on": self.state.on,
            "brightness": self.state.brightness,
            "color": list(self.state.color),
            "caps": {
                "color": self.caps.color,
                "brightness": self.caps.brightness,
                "can_strobe": self.caps.can_strobe,
            },
        }
