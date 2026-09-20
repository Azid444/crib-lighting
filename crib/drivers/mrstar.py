"""MR-Star TV backlight over Bluetooth LE.

These controllers are rebadged generic boards. The two protocols that cover
almost all of them are implemented below; set `protocol` in config.yaml if the
default does not work. `python -m crib.scan` prints the services of a nearby
device so you can tell which one you have.
"""
from __future__ import annotations

import asyncio

from bleak import BleakClient

from .base import Caps, Light, State

# "Triones" / Happy Lighting / LEDBLE boards.
TRIONES_CHAR = "0000ffd9-0000-1000-8000-00805f9b34fb"
# "LEDnetWF" boards (newer MR-Star stock).
LEDNET_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"


class MrStarLight(Light):
    # BLE without response tops out well under WiFi, and pushing harder just
    # fills the controller's buffer and makes it stutter.
    caps = Caps(color=True, brightness=True, max_hz=10.0)

    def __init__(
        self, id: str, name: str, address: str, protocol: str = "triones",
        char: str | None = None,
    ) -> None:
        super().__init__(id, name)
        self.address = address
        self.protocol = protocol
        # Some boards speak a known protocol on a different characteristic
        # (fff3 and ffe1 both turn up). Probing finds the real one, and it
        # overrides the protocol's default here.
        self._char = char or (
            TRIONES_CHAR if protocol == "triones" else LEDNET_CHAR
        )
        self._client: BleakClient | None = None
        self._seq = 0

    async def connect(self) -> None:
        self._client = BleakClient(self.address, timeout=15.0)
        await self._client.connect()
        self.available = True

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self.available = False

    def _packets(self, state: State) -> list[bytes]:
        if not state.on:
            return [bytes([0xCC, 0x24, 0x33])] if self.protocol == "triones" else [
                self._lednet(bytes([0x71, 0x00, 0x0F]))
            ]

        # These boards have no brightness register, so scale the colour itself.
        r, g, b = (round(c * state.brightness / 255) for c in state.color)
        if self.protocol == "triones":
            return [
                bytes([0xCC, 0x23, 0x33]),
                bytes([0x56, r, g, b, 0x00, 0xF0, 0xAA]),
            ]
        return [
            self._lednet(bytes([0x71, 0x01, 0x0F])),
            self._lednet(bytes([0x41, r, g, b, 0x00, 0x00, 0x00, 0x00, 0xF0, 0x0F])),
        ]

    def _lednet(self, payload: bytes) -> bytes:
        """Wrap a LEDnetWF payload in its sequence/length header + checksum."""
        self._seq = (self._seq + 1) % 256
        body = bytes([self._seq, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, len(payload)]) + payload
        return body + bytes([sum(payload) & 0xFF])

    async def _apply(self, state: State) -> None:
        if self._client is None or not self._client.is_connected:
            await self.connect()
        assert self._client is not None
        for pkt in self._packets(state):
            # response=False keeps the frame rate usable; these boards do not
            # acknowledge writes anyway.
            await self._client.write_gatt_char(self._char, pkt, response=False)
            await asyncio.sleep(0.01)
