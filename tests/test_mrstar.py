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


@pytest.mark.parametrize("protocol", MrStarLight.PROTOCOLS)
def test_every_dialect_carries_the_colour(protocol):
    """Whatever the framing, the three colour bytes must appear in order."""
    joined = b"".join(light(protocol)._packets(
        State(on=True, brightness=255, color=(11, 22, 33))))
    assert bytes([11, 22, 33]) in joined


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
