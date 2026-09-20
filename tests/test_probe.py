"""Identifying an unnamed BLE light by connecting to it."""
import pytest

from crib.probe import WRITE_CHARS, find_the_light, is_private_address, rank

TRIONES = "0000ffd9-0000-1000-8000-00805f9b34fb"
LEDNET = "0000ff01-0000-1000-8000-00805f9b34fb"


def dev(address, name="", likely=False, rssi=-60):
    return {"address": address, "name": name, "_likely": likely, "rssi": rssi}


# --- ordering -------------------------------------------------------------

def test_rotating_privacy_addresses_are_recognised():
    """Phones and watches use these; LED controllers do not."""
    assert is_private_address("4D:8B:DD:C0:17:C9")
    assert is_private_address("7A:2D:DE:15:9A:BE")
    assert not is_private_address("8C:26:AA:BA:A3:3F")
    assert not is_private_address("FF:23:12:04:20:F3")


def test_malformed_address_is_not_treated_as_private():
    assert not is_private_address("nonsense")


def test_likely_lights_are_tried_first():
    order = sorted([dev("11:00:00:00:00:01"),
                    dev("22:00:00:00:00:02", likely=True)], key=rank)
    assert order[0]["address"] == "22:00:00:00:00:02"


def test_suggestive_names_outrank_unnamed_devices():
    order = sorted([dev("11:00:00:00:00:01", ""),
                    dev("22:00:00:00:00:02", "GATT--DEMO")], key=rank)
    assert order[0]["name"] == "GATT--DEMO"


def test_phones_are_tried_last():
    """A room full of iPhones should not be probed before the light."""
    order = sorted([dev("4D:8B:DD:C0:17:C9", rssi=-40),
                    dev("8C:26:AA:BA:A3:3F", rssi=-80)], key=rank)
    assert order[0]["address"] == "8C:26:AA:BA:A3:3F"


def test_stronger_signal_wins_all_else_equal():
    order = sorted([dev("8C:00:00:00:00:01", rssi=-90),
                    dev("8C:00:00:00:00:02", rssi=-45)], key=rank)
    assert order[0]["rssi"] == -45


# --- the sweep ------------------------------------------------------------

@pytest.mark.asyncio
async def test_stops_at_the_first_device_that_answers(monkeypatch):
    calls = []

    async def fake_probe(address, timeout=12.0):
        calls.append(address)
        ok = address == "8C:26:AA:BA:A3:3F"
        return {"address": address, "ok": ok,
                "char": TRIONES if ok else None,
                "protocol": "triones" if ok else None,
                "error": None if ok else "no colour characteristic",
                "services": []}

    monkeypatch.setattr("crib.probe.probe", fake_probe)
    result = await find_the_light([
        dev("4D:8B:DD:C0:17:C9"),             # a phone, tried last
        dev("8C:26:AA:BA:A3:3F"),             # the light
        dev("CC:2F:4E:87:1D:5F"),
    ])

    assert result["found"]["address"] == "8C:26:AA:BA:A3:3F"
    assert result["found"]["protocol"] == "triones"
    assert result["found"]["char"] == TRIONES
    assert result["found"]["driver"] == "mrstar"
    assert "4D:8B:DD:C0:17:C9" not in calls, "probed a phone before the light"


@pytest.mark.asyncio
async def test_reports_every_attempt_when_nothing_matches(monkeypatch):
    async def fake_probe(address, timeout=12.0):
        return {"address": address, "ok": False, "char": None,
                "protocol": None, "error": "connection refused", "services": []}

    monkeypatch.setattr("crib.probe.probe", fake_probe)
    result = await find_the_light([dev("11:00:00:00:00:01"),
                                   dev("22:00:00:00:00:02")])
    assert result["found"] is None
    assert len(result["tried"]) == 2
    assert all(t["error"] == "connection refused" for t in result["tried"])


@pytest.mark.asyncio
async def test_sweep_is_capped(monkeypatch):
    """Each probe is a real BLE connection, so it must not try forever."""
    async def fake_probe(address, timeout=12.0):
        return {"address": address, "ok": False, "char": None,
                "protocol": None, "error": "nope", "services": []}

    monkeypatch.setattr("crib.probe.probe", fake_probe)
    result = await find_the_light(
        [dev(f"8C:00:00:00:00:{i:02x}") for i in range(40)], limit=5)
    assert len(result["tried"]) == 5


@pytest.mark.asyncio
async def test_empty_candidate_list_is_safe():
    assert (await find_the_light([]))["found"] is None


# --- driver plumbing ------------------------------------------------------

def test_every_known_char_maps_to_a_real_protocol():
    assert set(WRITE_CHARS.values()) <= {"triones", "lednet"}


def test_driver_accepts_a_probed_characteristic():
    """A board using fff3 must be drivable, not just the two defaults."""
    pytest.importorskip("bleak")
    from crib.drivers.mrstar import MrStarLight

    odd = "0000fff3-0000-1000-8000-00805f9b34fb"
    light = MrStarLight("tv", "TV", "AA:BB", protocol="triones", char=odd)
    assert light._char == odd
    # Still emits the Triones packets, just on a different characteristic.
    from crib.drivers.base import State
    assert light._packets(State(on=True, brightness=255, color=(255, 0, 0)))[1][0] == 0x56


def test_driver_defaults_when_no_char_is_given():
    pytest.importorskip("bleak")
    from crib.drivers.mrstar import MrStarLight

    assert MrStarLight("tv", "TV", "AA:BB", protocol="lednet")._char == LEDNET
    assert MrStarLight("tv", "TV", "AA:BB")._char == TRIONES
