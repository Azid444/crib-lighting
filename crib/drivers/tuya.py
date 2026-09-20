"""Tuya wall switch over the local LAN protocol (no Tuya cloud at runtime).

You need the device's local key once, from `python -m tinytuya wizard`. After
that every command goes straight to the device on your own network.
"""
from __future__ import annotations

import asyncio

import tinytuya

from .base import Caps, Light, State


class TuyaSwitch(Light):
    # A mechanical relay. Switching it fast would wreck it, so the effect
    # engine is told it cannot strobe and will hold it steady instead.
    caps = Caps(color=False, brightness=False, max_hz=0.5)

    def __init__(
        self,
        id: str,
        name: str,
        device_id: str,
        address: str,
        local_key: str,
        version: float = 3.3,
        switch_dp: int = 1,
    ) -> None:
        super().__init__(id, name)
        self.switch_dp = switch_dp
        self._dev = tinytuya.OutletDevice(device_id, address, local_key)
        self._dev.set_version(version)
        self._dev.set_socketPersistent(True)

    async def connect(self) -> None:
        status = await asyncio.to_thread(self._dev.status)
        if "Error" in status:
            raise RuntimeError(status["Error"])
        dps = status.get("dps", {})
        self.state = State(on=bool(dps.get(str(self.switch_dp), False)))
        self.available = True

    async def disconnect(self) -> None:
        await asyncio.to_thread(self._dev.close)
        self.available = False

    async def _apply(self, state: State) -> None:
        # tinytuya is blocking sockets, so keep it off the event loop.
        await asyncio.to_thread(self._dev.set_status, state.on, self.switch_dp)
