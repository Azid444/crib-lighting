"""Protocol encoding for the MR-Star BLE board. No hardware needed."""
import pytest

bleak = pytest.importorskip("bleak")

from crib.drivers.base import State
from crib.drivers.mrstar import MrStarLight


def light(protocol="triones"):
    return MrStarLight("tv", "TV", "AA:BB:CC:DD:EE:FF", protocol=protocol)


def test_triones_power_off():
    assert light()._packets(State(on=False)) == [bytes([0xCC, 0x24, 0x33])]


def test_triones_colour_packet():
    pkts = light()._packets(State(on=True, brightness=255, color=(255, 0, 128)))
    assert pkts[0] == bytes([0xCC, 0x23, 0x33])          # power on first
    assert pkts[1] == bytes([0x56, 255, 0, 128, 0x00, 0xF0, 0xAA])


def test_brightness_scales_colour():
    """The board has no brightness register, so colour must be scaled."""
    pkt = light()._packets(State(on=True, brightness=128, color=(255, 255, 255)))[1]
    assert pkt[1:4] == bytes([128, 128, 128])


def test_brightness_zero_is_black_not_a_crash():
    pkt = light()._packets(State(on=True, brightness=0, color=(255, 0, 0)))[1]
    assert pkt[1:4] == bytes([0, 0, 0])


def test_lednet_header_and_checksum():
    pkt = light("lednet")._packets(State(on=True, brightness=255, color=(10, 20, 30)))[1]
    payload = pkt[8:-1]                      # 8-byte header, then payload
    assert pkt[7] == len(payload)            # length byte
    assert pkt[-1] == sum(payload) & 0xFF    # checksum
    assert payload[1:4] == bytes([10, 20, 30])


def test_lednet_sequence_increments():
    l = light("lednet")
    # An "on" state emits two packets, so the counter advances by two.
    first = [p[0] for p in l._packets(State(on=True))]
    second = [p[0] for p in l._packets(State(on=True))]
    assert first == [1, 2] and second == [3, 4]


def test_all_packet_bytes_are_valid():
    """Any byte >255 would raise at write time on real hardware."""
    for proto in MrStarLight.PROTOCOLS:
        for bri in (0, 1, 127, 255):
            for col in ((0, 0, 0), (255, 255, 255), (37, 200, 91)):
                for pkt in light(proto)._packets(State(True, bri, col)):
                    assert all(0 <= b <= 255 for b in pkt)


# findn is the exception: it sends hue and saturation, not RGB.
RGB_DIALECTS = [p for p in MrStarLight.PROTOCOLS if p != "findn"]


@pytest.mark.parametrize("protocol", RGB_DIALECTS)
def test_rgb_dialects_carry_the_colour_bytes(protocol):
    """Whatever the framing, the three colour bytes must appear in order."""
    joined = b"".join(light(protocol)._packets(
        State(on=True, brightness=255, color=(11, 22, 33))))
    assert bytes([11, 22, 33]) in joined


@pytest.mark.parametrize("protocol", MrStarLight.PROTOCOLS)
def test_every_dialect_distinguishes_colours(protocol):
    red = b"".join(light(protocol)._packets(State(True, 255, (255, 0, 0))))
    blue = b"".join(light(protocol)._packets(State(True, 255, (0, 0, 255))))
    assert red != blue


def test_findn_sends_hue_and_saturation():
    """Pure red is hue 0, fully saturated; the board wants 0..360 / 0..1000."""
    pkts = light("findn")._packets(State(True, 255, (255, 0, 0)))
    colour = next(p for p in pkts if p[1] == 0x04)
    assert colour[3:5] == bytes([0, 0])                  # hue 0
    assert int.from_bytes(colour[5:7], "big") == 1000    # saturation


def test_findn_hue_wraps_within_two_bytes():
    """Magenta is hue 300, which must not overflow the high byte."""
    colour = next(p for p in light("findn")._packets(
        State(True, 255, (255, 0, 255))) if p[1] == 0x04)
    assert int.from_bytes(colour[3:5], "big") == 300


def test_findn_uses_its_brightness_register_not_the_colour():
    """Half brightness must dim via 0x05, leaving the hue untouched."""
    pkts = light("findn")._packets(State(True, 128, (255, 0, 0)))
    colour = next(p for p in pkts if p[1] == 0x04)
    bright = next(p for p in pkts if p[1] == 0x05)
    assert int.from_bytes(colour[5:7], "big") == 1000
    assert 100 <= int.from_bytes(bright[3:5], "big") <= 1000


def test_findn_skips_unchanged_power_and_brightness():
    """A rave sends 20 frames a second; only the colour should repeat."""
    l = light("findn")
    first = l._packets(State(True, 255, (255, 0, 0)))
    second = l._packets(State(True, 255, (0, 255, 0)))
    assert len(first) == 3 and len(second) == 1
    assert second[0][1] == 0x04


def test_findn_resends_power_after_an_off():
    l = light("findn")
    l._packets(State(True, 255, (255, 0, 0)))
    l._packets(State(on=False))
    assert l._packets(State(True, 255, (255, 0, 0)))[0][3] == 0x01


@pytest.mark.parametrize("protocol", MrStarLight.PROTOCOLS)
def test_every_dialect_can_turn_off(protocol):
    assert light(protocol)._packets(State(on=False))


def test_unknown_protocol_is_rejected_loudly():
    """A typo in config.yaml should say so, not silently send nothing."""
    with pytest.raises(ValueError):
        light("nonsense")._packets(State(on=True))


def test_each_dialect_has_a_default_characteristic():
    from crib.drivers.mrstar import DEFAULT_CHARS

    assert set(MrStarLight.PROTOCOLS) <= set(DEFAULT_CHARS)


@pytest.mark.asyncio
async def test_connect_resolves_by_name_not_just_address(monkeypatch):
    """These boards change address on every power cycle; the name does not."""
    import crib.probe

    asked = []

    async def fake_resolve(target, timeout=10.0):
        asked.append(target)
        return "device-object"

    seen = {}

    class FakeClient:
        def __init__(self, target, timeout=None):
            seen["target"] = target

        async def connect(self):
            return None

    monkeypatch.setattr(crib.probe, "resolve", fake_resolve)
    monkeypatch.setattr("crib.drivers.mrstar.BleakClient", FakeClient)

    l = MrStarLight("tv", "TV", "GATT--DEMO", protocol="findn")
    await l.connect()
    assert asked == ["GATT--DEMO"]
    assert seen["target"] == "device-object"
    assert l.available is True


@pytest.mark.asyncio
async def test_connect_falls_back_to_the_raw_address(monkeypatch):
    """If the scan misses it, connecting by address may still work."""
    import crib.probe

    async def no_luck(target, timeout=10.0):
        return None

    seen = {}

    class FakeClient:
        def __init__(self, target, timeout=None):
            seen["target"] = target

        async def connect(self):
            return None

    monkeypatch.setattr(crib.probe, "resolve", no_luck)
    monkeypatch.setattr("crib.drivers.mrstar.BleakClient", FakeClient)

    await MrStarLight("tv", "TV", "AA:BB:CC:DD:EE:FF").connect()
    assert seen["target"] == "AA:BB:CC:DD:EE:FF"
