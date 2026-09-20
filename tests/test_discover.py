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


# --- BLE matching ---------------------------------------------------------

from crib.discover import _ble_entry, discover_all, find_tuya


def test_service_uuid_beats_a_useless_name():
    """The reliable signal: a controller advertising the Triones service."""
    e = _ble_entry("AA:BB", "BLE-2842", ["0000ffd9-0000-1000-8000-00805f9b34fb"])
    assert e["_likely"] and e["protocol"] == "triones"


def test_lednet_service_selects_that_protocol():
    e = _ble_entry("AA:BB", "", ["0000ff01-0000-1000-8000-00805f9b34fb"])
    assert e["_likely"] and e["protocol"] == "lednet"


def test_uuid_case_does_not_matter():
    e = _ble_entry("AA:BB", "", ["0000FFD9-0000-1000-8000-00805F9B34FB"])
    assert e["_likely"]


def test_name_match_still_works_without_uuids():
    assert _ble_entry("AA:BB", "MRSTAR-99", [])["_likely"]
    assert _ble_entry("AA:BB", "LEDnetWF01", [])["protocol"] == "lednet"


def test_unknown_devices_are_kept_but_not_flagged():
    """A pair of headphones should be offered, not hidden and not guessed."""
    e = _ble_entry("AA:BB", "AirPods", [])
    assert not e["_likely"]
    assert e["address"] == "AA:BB" and e["driver"] == "mrstar"


def test_unnamed_device_is_described_by_address():
    assert "AA:BB" in _ble_entry("AA:BB", "", [])["_detail"]


# --- diagnostics ----------------------------------------------------------

@pytest.mark.asyncio
async def test_tuya_reports_why_it_heard_nothing(monkeypatch):
    found, note = await find_tuya(timeout=0.05)
    assert found == []
    assert note and ("6666" in note or "ports" in note)


@pytest.mark.asyncio
async def test_tuya_reports_a_blocked_port(monkeypatch):
    """Binding failures must be surfaced, not silently swallowed."""
    async def boom(*a, **kw):
        raise OSError("address already in use")

    loop = asyncio.get_event_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", boom)
    found, note = await find_tuya(timeout=0.05)
    assert found == [] and "already in use" in note


@pytest.mark.asyncio
async def test_discover_all_explains_every_empty_category(monkeypatch):
    async def no_wled(*a, **kw): return []
    async def no_ble(*a, **kw): return [], [], "Bluetooth is off"
    async def no_tuya(*a, **kw): return [], "firewall maybe"

    monkeypatch.setattr("crib.discover.find_wled", no_wled)
    monkeypatch.setattr("crib.discover.find_mrstar", no_ble)
    monkeypatch.setattr("crib.discover.find_tuya", no_tuya)

    result = await discover_all(timeout=0.1)
    assert result["notes"]["wled"] and result["notes"]["mrstar"]
    assert result["notes"]["tuya"] == "firewall maybe"
    assert result["candidates"] == []


@pytest.mark.asyncio
async def test_one_finder_crashing_does_not_sink_the_others(monkeypatch):
    async def wled(*a, **kw):
        return [{"driver": "wled", "id": "strip", "host": "1.2.3.4"}]
    async def ble_boom(*a, **kw): raise RuntimeError("no adapter")
    async def no_tuya(*a, **kw): return [], None

    monkeypatch.setattr("crib.discover.find_wled", wled)
    monkeypatch.setattr("crib.discover.find_mrstar", ble_boom)
    monkeypatch.setattr("crib.discover.find_tuya", no_tuya)

    result = await discover_all(timeout=0.1)
    assert len(result["wled"]) == 1
    assert result["mrstar"] == [] and "no adapter" in result["notes"]["mrstar"]


@pytest.mark.asyncio
async def test_candidates_are_passed_through(monkeypatch):
    other = _ble_entry("AA:BB", "AirPods", [])
    async def no_wled(*a, **kw): return []
    async def ble(*a, **kw): return [], [other], "none identified"
    async def no_tuya(*a, **kw): return [], None

    monkeypatch.setattr("crib.discover.find_wled", no_wled)
    monkeypatch.setattr("crib.discover.find_mrstar", ble)
    monkeypatch.setattr("crib.discover.find_tuya", no_tuya)

    result = await discover_all(timeout=0.1)
    assert result["candidates"] == [other]


def test_advertised_services_map_to_real_protocols():
    """A service UUID must never name a dialect the driver cannot encode."""
    from crib.discover import BLE_SERVICES
    from crib.drivers.mrstar import MrStarLight

    assert set(BLE_SERVICES.values()) <= set(MrStarLight.PROTOCOLS)
