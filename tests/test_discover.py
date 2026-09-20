"""Device discovery. Uses a real local HTTP server and synthetic packets."""
import asyncio
import json
import struct

import pytest

from crib.discover import (find_wled, guess_protocol, local_ipv4,
                           parse_tuya_broadcast)


def tuya_packet(payload: bytes) -> bytes:
    """Build a well-formed Tuya UDP broadcast frame."""
    return (b"\x00\x00\x55\xaa"
            + struct.pack(">I", 0)              # sequence
            + struct.pack(">I", 0)              # command
            + struct.pack(">I", len(payload) + 8)
            + payload
            + b"\x00\x00\x00\x00"               # crc
            + b"\x00\x00\xaa\x55")


# --- Tuya ----------------------------------------------------------------

def test_parses_a_plaintext_beacon():
    body = json.dumps({"gwId": "abc123", "ip": "192.168.1.60",
                       "version": "3.3"}).encode()
    found = parse_tuya_broadcast(tuya_packet(body))
    assert found["device_id"] == "abc123"
    assert found["address"] == "192.168.1.60"
    assert found["version"] == 3.3
    assert found["driver"] == "tuya"


def test_local_key_is_left_empty_for_the_user():
    """The key is never broadcast; it must come from the Tuya account."""
    body = json.dumps({"gwId": "x", "ip": "1.2.3.4"}).encode()
    assert parse_tuya_broadcast(tuya_packet(body))["local_key"] == ""


def test_garbage_packets_are_ignored():
    for bad in (b"", b"hello", b"\x00\x00\x55\xaa", b"\xff" * 40,
                tuya_packet(b"not json")):
        assert parse_tuya_broadcast(bad) is None


def test_beacon_without_required_fields_is_ignored():
    assert parse_tuya_broadcast(tuya_packet(json.dumps({"ip": "1.2.3.4"}).encode())) is None
    assert parse_tuya_broadcast(tuya_packet(json.dumps({"gwId": "x"}).encode())) is None


def test_missing_version_defaults_sensibly():
    body = json.dumps({"gwId": "x", "ip": "1.2.3.4"}).encode()
    assert parse_tuya_broadcast(tuya_packet(body))["version"] == 3.3


# --- BLE protocol guess ---------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("LEDnetWF0200", "lednet"),
    ("lednetwf", "lednet"),
    ("MRSTAR-1234", "triones"),
    ("Triones-ABC", "triones"),
    ("", "triones"),
])
def test_protocol_guess(name, expected):
    assert guess_protocol(name) == expected


# --- WLED sweep -----------------------------------------------------------

def test_local_ipv4_returns_something_usable():
    ip = local_ipv4()
    assert ip is None or ip.count(".") == 3


@pytest.mark.asyncio
async def test_wled_sweep_finds_a_real_responder(monkeypatch):
    """Stand up a fake WLED on loopback and confirm the sweep identifies it."""
    from aiohttp import web

    async def info(request):
        return web.json_response(
            {"ver": "0.14.0", "name": "Desk Strip", "leds": {"count": 120}}
        )

    app = web.Application()
    app.router.add_get("/json/info", info)
    runner = web.AppRunner(app)
    await runner.setup()
    # Port 80 is what the sweep probes, so serve there on loopback.
    try:
        site = web.TCPSite(runner, "127.0.0.1", 80)
        await site.start()
    except PermissionError:
        pytest.skip("cannot bind port 80 in this environment")

    monkeypatch.setattr("crib.discover.local_ipv4", lambda: "127.0.0.1")
    try:
        found = await find_wled(timeout=4.0)
    finally:
        await runner.cleanup()

    assert any(d["host"] == "127.0.0.1" for d in found), found
    strip = next(d for d in found if d["host"] == "127.0.0.1")
    assert strip["name"] == "Desk Strip" and strip["driver"] == "wled"
    assert "120 LEDs" in strip["_detail"]


@pytest.mark.asyncio
async def test_non_wled_http_servers_are_not_matched(monkeypatch):
    """A printer or router web UI must not be mistaken for a light."""
    from aiohttp import web

    async def info(request):
        return web.json_response({"hello": "i am a router"})

    app = web.Application()
    app.router.add_get("/json/info", info)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 80)
        await site.start()
    except PermissionError:
        pytest.skip("cannot bind port 80 in this environment")

    monkeypatch.setattr("crib.discover.local_ipv4", lambda: "127.0.0.1")
    try:
        assert await find_wled(timeout=4.0) == []
    finally:
        await runner.cleanup()
