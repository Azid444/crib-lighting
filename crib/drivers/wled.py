"""WLED strip over its local JSON API. No cloud, no account."""
from __future__ import annotations

import aiohttp

from .base import Caps, Light, State


class WledLight(Light):
    # WLED on an ESP32 comfortably absorbs ~20 state posts/sec over the LAN.
    caps = Caps(color=True, brightness=True, max_hz=20.0)

    def __init__(self, id: str, name: str, host: str) -> None:
        super().__init__(id, name)
        self.host = host
        self._session: aiohttp.ClientSession | None = None

    async def connect(self) -> None:
        # Short timeouts: a stalled request would stall the whole effect frame.
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=1.0)
        )
        async with self._session.get(f"http://{self.host}/json/state") as r:
            data = await r.json()
        self.state = State(
            on=bool(data.get("on")),
            brightness=int(data.get("bri", 255)),
            color=tuple(data["seg"][0]["col"][0][:3]) if data.get("seg") else (255, 255, 255),
        )
        self.available = True

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        self.available = False

    async def _apply(self, state: State) -> None:
        assert self._session is not None, "connect() first"
        payload = {
            "on": state.on,
            "bri": state.brightness,
            # fx 0 = solid; we drive the animation ourselves so every device
            # in the room stays on the same beat.
            "seg": [{"col": [list(state.color)], "fx": 0}],
        }
        async with self._session.post(
            f"http://{self.host}/json/state", json=payload
        ) as r:
            r.raise_for_status()
