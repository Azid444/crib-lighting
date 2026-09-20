"""MR-Star TV backlight over Bluetooth LE.

These controllers are rebadged generic boards. The two protocols that cover
almost all of them are implemented below; set `protocol` in config.yaml if the
default does not work. `python -m crib.scan` prints the services of a nearby
device so you can tell which one you have.
"""
from __future__ import annotations

import asyncio
import colorsys

from bleak import BleakClient

from .base import Caps, Light, State

# "Triones" / Happy Lighting / LEDBLE boards.
TRIONES_CHAR = "0000ffd9-0000-1000-8000-00805f9b34fb"
# "LEDnetWF" boards (newer MR-Star stock).
LEDNET_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"
# ELK-BLEDOM clones and FINDN boards (the "GATT--DEMO" MR-Star stock) both
# take their commands here, under service fff0.
ELK_CHAR = "0000fff3-0000-1000-8000-00805f9b34fb"

# Where each dialect normally lives, when config.yaml does not say.
DEFAULT_CHARS = {
    "findn": ELK_CHAR,
    "triones": TRIONES_CHAR,
    "lednet": LEDNET_CHAR,
    "lednetwf": LEDNET_CHAR,
    "elk": ELK_CHAR,
    "elk_alt": ELK_CHAR,
    "raw": ELK_CHAR,
}


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
        self._char = char or DEFAULT_CHARS.get(protocol, TRIONES_CHAR)
        self._client: BleakClient | None = None
        self._seq = 0
        self._last_power: bool | None = None
        self._last_bri: int | None = None

    async def connect(self) -> None:
        # Windows will not connect by address alone: WinRT needs a recent
        # advertisement or it reports the device as not found. resolve() also
        # accepts a name, which matters because these boards use random static
        # addresses that change whenever the light is power-cycled -- putting
        # the name in config.yaml survives that. Falling back to the raw
        # address keeps things working if the scan happens to miss it.
        from ..probe import resolve

        target = None
        try:
            target = await resolve(self.address, timeout=10.0)
        except Exception:
            pass
        self._client = BleakClient(target or self.address, timeout=15.0)
        await self._client.connect()
        # The board's own state is unknown after a reconnect.
        self._last_power, self._last_bri = None, None
        self.available = True

    async def disconnect(self) -> None:
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        self.available = False

    # Dialects, best-attested first. A board only speaks one of them, and
    # the name alone does not say which -- probing does.
    PROTOCOLS = ("findn", "triones", "elk", "elk_alt", "lednet", "lednetwf",
                 "raw")

    def _packets(self, state: State) -> list[bytes]:
        """Encode a state in whichever dialect this board speaks."""
        encode = getattr(self, "_p_" + self.protocol, None)
        if encode is None:
            raise ValueError(f"unknown protocol {self.protocol!r}")
        return encode(state)

    @staticmethod
    def _scaled(state: State) -> tuple[int, int, int]:
        """Colour with brightness folded in.

        Most of these dialects have no brightness register, so dimming has to
        happen in the colour itself.
        """
        return tuple(round(c * state.brightness / 255) for c in state.color)

    def _p_findn(self, state):
        """FINDN LTD boards, which advertise as GATT--DEMO.

        The odd one out: it takes hue and saturation rather than RGB, and it
        does have a real brightness register, so the colour is sent at full
        strength and dimmed separately. Power and brightness are only re-sent
        when they change, which keeps a rave down to one packet per frame.
        """
        if not state.on:
            self._last_power, self._last_bri = False, None
            return [bytes([0xBC, 0x01, 0x01, 0x00, 0x55])]

        out = []
        if self._last_power is not True:
            self._last_power = True
            out.append(bytes([0xBC, 0x01, 0x01, 0x01, 0x55]))

        h, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in state.color))
        hue, sat = round(h * 360) % 360, round(s * 1000)
        out.append(bytes([0xBC, 0x04, 0x06, hue // 256, hue % 256,
                          sat // 256, sat % 256, 0x00, 0x00, 0x55]))

        # 1..255 onto the board's 100..1000 scale.
        bri = 100 + round(state.brightness * 900 / 255)
        if bri != self._last_bri:
            self._last_bri = bri
            out.append(bytes([0xBC, 0x05, 0x06, bri // 256, bri % 256,
                              0x00, 0x00, 0x00, 0x00, 0x55]))
        return out

    def _p_triones(self, state):
        on, (r, g, b) = state.on, self._scaled(state)
        if not on:
            return [bytes([0xCC, 0x24, 0x33])]
        return [bytes([0xCC, 0x23, 0x33]),
                bytes([0x56, r, g, b, 0x00, 0xF0, 0xAA])]

    def _p_elk(self, state):
        on, (r, g, b) = state.on, self._scaled(state)
        """ELK-BLEDOM and the many boards that clone it. Usually on fff3."""
        if not on:
            return [bytes([0x7E, 0x00, 0x04, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xEF])]
        return [bytes([0x7E, 0x00, 0x04, 0xF0, 0x00, 0x01, 0xFF, 0x00, 0xEF]),
                bytes([0x7E, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xEF])]

    def _p_elk_alt(self, state):
        on, (r, g, b) = state.on, self._scaled(state)
        """Same family, later firmware: different leading and trailing bytes."""
        if not on:
            return [bytes([0x7E, 0x07, 0x04, 0x00, 0x00, 0x00, 0x10, 0x00, 0xEF])]
        return [bytes([0x7E, 0x07, 0x04, 0xF0, 0x00, 0x01, 0x10, 0x00, 0xEF]),
                bytes([0x7E, 0x07, 0x05, 0x03, r, g, b, 0x10, 0xEF])]

    def _p_lednet(self, state):
        on, (r, g, b) = state.on, self._scaled(state)
        if not on:
            return [self._lednet(bytes([0x71, 0x00, 0x0F]))]
        return [self._lednet(bytes([0x71, 0x01, 0x0F])),
                self._lednet(bytes([0x41, r, g, b, 0x00, 0x00, 0x00, 0x00,
                                    0xF0, 0x0F]))]

    def _p_lednetwf(self, state):
        on, (r, g, b) = state.on, self._scaled(state)
        """The long-form framing newer LEDnetWF firmware wants."""
        if not on:
            return [self._wf(bytes([0x0D, 0x0E, 0x0B, 0x3B, 0x24, 0x00, 0x00,
                                    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x32,
                                    0x00, 0x00, 0x90]))]
        return [self._wf(bytes([0x0D, 0x0E, 0x0B, 0x3B, 0x23, 0x00, 0x00, 0x00,
                                0x00, 0x00, 0x00, 0x00, 0x00, 0x32, 0x00, 0x00,
                                0x91])),
                self._wf(bytes([0x0D, 0x0E, 0x0B, 0x3B, 0x41, 0x01, r, g, b,
                                0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x3D,
                                0x00, 0x00]))]

    def _p_raw(self, state):
        """Serial-passthrough modules that expect the three bytes and nothing else."""
        r, g, b = self._scaled(state)
        return [bytes([r, g, b]) if state.on else bytes([0, 0, 0])]

    def _lednet(self, payload: bytes) -> bytes:
        """Wrap a LEDnetWF payload in its sequence/length header + checksum."""
        self._seq = (self._seq + 1) % 256
        body = bytes([self._seq, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, len(payload)]) + payload
        return body + bytes([sum(payload) & 0xFF])

    def _wf(self, payload: bytes) -> bytes:
        """Long-form framing: leading zero, sequence, then the payload verbatim."""
        self._seq = (self._seq + 1) % 256
        return bytes([0x00, self._seq, 0x80, 0x00, 0x00]) + payload

    async def _apply(self, state: State) -> None:
        if self._client is None or not self._client.is_connected:
            await self.connect()
        assert self._client is not None
        for pkt in self._packets(state):
            # response=False keeps the frame rate usable; these boards do not
            # acknowledge writes anyway.
            await self._client.write_gatt_char(self._char, pkt, response=False)
            await asyncio.sleep(0.01)
