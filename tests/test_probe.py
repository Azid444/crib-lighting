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
    from crib.drivers.mrstar import MrStarLight

    assert set(WRITE_CHARS.values()) <= set(MrStarLight.PROTOCOLS)


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


# --- GATT inspection ------------------------------------------------------

from crib.probe import writable_candidates

CHARS = [
    {"uuid": "00002a00-0000-1000-8000-00805f9b34fb", "writable": False,
     "properties": ["read"]},
    {"uuid": "0000abcd-0000-1000-8000-00805f9b34fb", "writable": True,
     "properties": ["write"]},
    {"uuid": TRIONES, "writable": True, "properties": ["write-without-response"]},
]


def test_known_characteristics_are_tried_first():
    order = writable_candidates(CHARS)
    assert order[0]["uuid"] == TRIONES
    assert [c["uuid"] for c in order] == [TRIONES, "0000abcd-0000-1000-8000-00805f9b34fb"]


def test_unwritable_characteristics_are_excluded():
    """Flashing a read-only characteristic would only produce errors."""
    assert all(c["writable"] for c in writable_candidates(CHARS))


def test_a_device_with_nothing_writable_yields_nothing():
    assert writable_candidates([CHARS[0]]) == []


@pytest.mark.asyncio
async def test_probe_reports_the_hunt_command_when_unrecognised(monkeypatch):
    """A connectable device with odd characteristics must not be a dead end."""
    async def fake_describe(address, timeout=15.0, attempts=2):
        return {"address": address, "ok": True, "error": None,
                "characteristics": CHARS[:2]}

    monkeypatch.setattr("crib.probe.describe", fake_describe)
    from crib.probe import probe

    result = await probe("8C:26:AA:BA:A3:3F")
    assert result["ok"] is False
    assert "--hunt 8C:26:AA:BA:A3:3F" in result["error"]
    assert "1 writable" in result["error"]


@pytest.mark.asyncio
async def test_probe_matches_a_known_characteristic(monkeypatch):
    async def fake_describe(address, timeout=15.0, attempts=2):
        return {"address": address, "ok": True, "error": None,
                "characteristics": CHARS}

    monkeypatch.setattr("crib.probe.describe", fake_describe)
    from crib.probe import probe

    result = await probe("AA:BB")
    assert result["ok"] and result["char"] == TRIONES
    assert result["protocol"] == "triones"


@pytest.mark.asyncio
async def test_probe_passes_through_a_connection_failure(monkeypatch):
    async def fake_describe(address, timeout=15.0, attempts=2):
        return {"address": address, "ok": False, "error": "Unreachable",
                "characteristics": []}

    monkeypatch.setattr("crib.probe.describe", fake_describe)
    from crib.probe import probe

    result = await probe("AA:BB")
    assert result["ok"] is False and result["error"] == "Unreachable"


# --- error guidance -------------------------------------------------------

from crib.probe import explain_error


@pytest.mark.parametrize("error", [
    "Could not get GATT services: Unreachable",
    "Device is busy",
    "Access denied",
])
def test_refused_connections_point_at_the_phone_app(error):
    hint = explain_error(error)
    assert hint and "phone" in hint


@pytest.mark.parametrize("error", [
    "Device with address E9:DD:AE:0B:9C:51 was not found.",
    "FF:23:12:04:20:F3 is not advertising - it may be powered off",
])
def test_not_advertising_is_diagnosed_separately(error):
    """A different cause to a refused connection, so different advice."""
    hint = explain_error(error)
    assert hint and "advertising" in hint


def test_unrelated_errors_get_no_invented_advice():
    assert explain_error("connected, but no known colour characteristic") is None
    assert explain_error(None) is None
    assert explain_error("") is None


@pytest.mark.asyncio
async def test_describe_scans_before_connecting(monkeypatch):
    """Windows rejects a connect by raw address without a fresh scan."""
    resolved = []

    async def fake_resolve(address, timeout=10.0):
        resolved.append(address)
        return None

    monkeypatch.setattr("crib.probe.resolve", fake_resolve)
    from crib.probe import describe

    result = await describe("FF:23:12:04:20:F3", attempts=2)
    assert resolved == ["FF:23:12:04:20:F3"] * 2, "did not rescan between tries"
    assert result["ok"] is False
    assert "not advertising" in result["error"]


@pytest.mark.asyncio
async def test_a_device_that_never_advertises_is_reported_clearly(monkeypatch):
    async def fake_resolve(address, timeout=10.0):
        return None

    monkeypatch.setattr("crib.probe.resolve", fake_resolve)
    from crib.probe import describe, explain_error

    result = await describe("AA:BB:CC:DD:EE:FF", attempts=1)
    assert "advertising" in explain_error(result["error"])


# --- resolving by address or name ----------------------------------------

from crib.probe import looks_like_address


@pytest.mark.parametrize("value,expected", [
    ("FF:23:12:04:20:F3", True),
    ("ff:23:12:04:20:f3", True),
    ("GATT--DEMO", False),
    ("", False),
    ("FF:23:12:04:20", False),
    ("6AFB142B-AE21-C121-A549-D2678CFB83AB", False),   # iOS identifier
])
def test_address_detection(value, expected):
    assert looks_like_address(value) is expected


class FakeDevice:
    def __init__(self, address):
        self.address = address
        self.name = ""


@pytest.mark.asyncio
async def test_resolves_by_name_when_the_address_changed(monkeypatch):
    """These chips get a new random address after a power cycle."""
    async def fake_nearby(timeout=10.0):
        return [{"address": "AA:11:22:33:44:55", "name": "GATT--DEMO",
                 "rssi": -70, "device": FakeDevice("AA:11:22:33:44:55")}]

    monkeypatch.setattr("crib.probe.nearby", fake_nearby)
    from crib.probe import resolve

    found = await resolve("GATT--DEMO")
    assert found.address == "AA:11:22:33:44:55"


@pytest.mark.asyncio
async def test_name_match_is_case_insensitive(monkeypatch):
    async def fake_nearby(timeout=10.0):
        return [{"address": "AA:11:22:33:44:55", "name": "GATT--DEMO",
                 "rssi": -70, "device": FakeDevice("AA:11:22:33:44:55")}]

    monkeypatch.setattr("crib.probe.nearby", fake_nearby)
    from crib.probe import resolve

    assert await resolve("gatt--demo") is not None


@pytest.mark.asyncio
async def test_a_stale_address_falls_back_to_a_full_scan(monkeypatch):
    """Address lookup missing must not end the search."""
    async def fake_nearby(timeout=10.0):
        return [{"address": "AA:11:22:33:44:55", "name": "other",
                 "rssi": -70, "device": FakeDevice("AA:11:22:33:44:55")}]

    async def no_such_address(address, timeout=10.0):
        return None

    import bleak
    monkeypatch.setattr(bleak.BleakScanner, "find_device_by_address",
                        staticmethod(no_such_address))
    monkeypatch.setattr("crib.probe.nearby", fake_nearby)
    from crib.probe import resolve

    assert await resolve("FF:23:12:04:20:F3") is None
    assert (await resolve("AA:11:22:33:44:55")).address == "AA:11:22:33:44:55"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_nothing_is_advertising(monkeypatch):
    async def fake_nearby(timeout=10.0):
        return []

    monkeypatch.setattr("crib.probe.nearby", fake_nearby)
    from crib.probe import resolve

    assert await resolve("GATT--DEMO") is None


# --- three-way answer -----------------------------------------------------

from crib.probe import _ask


@pytest.mark.parametrize("typed,expected", [
    ("y", "yes"), ("yes", "yes"), ("Y", "yes"),
    ("p", "partial"), ("partial", "partial"),
    ("n", "no"), ("", "no"), ("anything", "no"),
])
def test_answer_parsing(typed, expected, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: typed)
    assert _ask("?") == expected


def test_interrupting_the_prompt_exits_cleanly(monkeypatch):
    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(SystemExit):
        _ask("?")
