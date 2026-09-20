import asyncio

import pytest

from crib.effects import Breathe, Engine, Rave, Solid, Strobe, hsv
from crib.room import Room




def test_hsv_endpoints():
    assert hsv(0.0) == (255, 0, 0)
    assert hsv(1.0) == (255, 0, 0)  # wraps


async def test_relay_never_strobes(config):
    """The whole point of the capability system: protect the mechanical relay."""
    room = Room(config)
    relay = room.lights["ceiling"]
    for fx in (Strobe(), Rave()):
        for i in range(40):
            assert fx.frame(i * 0.05, relay) == {"on": False}


async def test_rave_shares_one_colour_across_devices(config):
    """Both strobe-capable lights must agree within a beat."""
    room = Room(config)
    rave = Rave(bpm=120)
    strip = room.lights["strip"]
    a = rave.frame(0.1, strip)
    b = rave.frame(0.1, strip)
    assert a["color"] == b["color"]


async def test_rave_changes_colour_between_beats():
    rave = Rave(bpm=120)  # 0.5s beats

    class L:
        id = "x"
        from crib.drivers.base import Caps
        caps = Caps(color=True, brightness=True, max_hz=100)

    seen = {tuple(rave.frame(t, L)["color"]) for t in (0.1, 0.6, 1.1, 1.6, 2.1)}
    assert len(seen) > 1


async def test_breathe_stays_lit():
    from crib.drivers.base import Caps

    class L:
        id = "x"
        caps = Caps(color=True, brightness=True, max_hz=100)

    levels = [Breathe(period=4).frame(t / 10, L)["brightness"] for t in range(40)]
    assert min(levels) >= 25 and max(levels) <= 255


async def test_engine_start_stop_and_swap(config):
    room = Room(config)
    await room.engine.start("rave", bpm=200)
    assert room.engine.running == "rave"
    await asyncio.sleep(0.25)
    assert room.lights["strip"].frames, "engine produced no frames"
    # Starting another effect must replace, not stack.
    await room.engine.start("solid", color=(1, 2, 3))
    assert room.engine.running == "solid"
    await room.engine.stop()
    assert room.engine.running is None
    n = len(room.lights["strip"].frames)
    await asyncio.sleep(0.15)
    assert len(room.lights["strip"].frames) == n, "loop kept running after stop"


async def test_failing_device_does_not_kill_the_loop(config):
    room = Room(config)
    room.lights["strip"].fail = True
    await room.engine.start("rave")
    await asyncio.sleep(0.25)
    assert room.engine.running == "rave"
    assert room.lights["strip"].available is False
    await room.engine.stop()


async def test_throttle_limits_slow_devices(config):
    room = Room(config)
    relay = room.lights["ceiling"]  # 0.5 Hz
    for _ in range(50):
        await relay.set(on=True)
    assert len(relay.frames) == 1, "relay was driven past its rate limit"
