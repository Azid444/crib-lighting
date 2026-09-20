import asyncio

import pytest

from crib.drivers import DRIVERS
from crib.drivers.base import Caps, Light, State


class FakeLight(Light):
    """In-memory device that records every frame it is asked to show."""

    caps = Caps(color=True, brightness=True, max_hz=1000.0)

    def __init__(self, id, name, fail=False):
        super().__init__(id, name)
        self.frames: list[State] = []
        self.fail = fail

    async def _apply(self, state: State) -> None:
        if self.fail:
            raise OSError("device unplugged")
        self.frames.append(State(state.on, state.brightness, state.color))


class FakeRelay(FakeLight):
    caps = Caps(color=False, brightness=False, max_hz=0.5)


@pytest.fixture
def config():
    return {
        "devices": [
            {"driver": "fake", "id": "strip", "name": "Strip"},
            {"driver": "fakerelay", "id": "ceiling", "name": "Ceiling"},
        ]
    }


@pytest.fixture(autouse=True)
def register_fakes(monkeypatch):
    import crib.drivers as d

    real = d.load

    def load(name):
        if name == "fake":
            return FakeLight
        if name == "fakerelay":
            return FakeRelay
        return real(name)

    monkeypatch.setattr(d, "load", load)
    # build() resolves load() through the module, so patch there too.
    monkeypatch.setattr(d, "build", lambda spec: load(dict(spec).pop("driver"))(
        **{k: v for k, v in spec.items() if k != "driver"}))
    import crib.room
    monkeypatch.setattr(crib.room, "build", d.build)
    yield
