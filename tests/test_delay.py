"""Latency compensation for Bluetooth and HDMI audio paths."""
import pytest

from crib.delay import Delayed


class Source:
    """Minimal stand-in for Audio / SpotifyBeats."""

    def __init__(self):
        self.beats = self.spikes = 0
        self.bass = self.mid = self.treble = self.level = 0.0
        self.bpm = 128.0
        self.running = True
        self.started = self.stopped = 0

    def start(self): self.started += 1
    def stop(self): self.stopped += 1
    def snapshot(self): return {"running": self.running, "bpm": self.bpm}


def test_beat_is_held_back_by_the_delay():
    src = Source()
    d = Delayed(src, delay_s=0.2)
    now = 100.0

    src.beats = 1                      # a kick happens now
    for _ in range(10):                # 50ms of ticks
        d.sample(now); now += 0.005
    assert d.beats == 0, "beat escaped before the delay elapsed"

    for _ in range(40):                # past 200ms total
        d.sample(now); now += 0.005
    assert d.beats == 1, "beat never arrived"


def test_zero_delay_passes_straight_through():
    src = Source()
    d = Delayed(src, delay_s=0.0)
    src.beats = 3
    d.sample(100.0)
    d.sample(100.005)
    assert d.beats == 3


def test_levels_are_delayed_too():
    """Brightness must not lead the sound any more than the beats do."""
    src = Source()
    d = Delayed(src, delay_s=0.1)
    now = 100.0
    src.bass = 0.09
    for _ in range(5):
        d.sample(now); now += 0.005
    assert d.bass == 0.0
    for _ in range(25):
        d.sample(now); now += 0.005
    assert d.bass == pytest.approx(0.09)


def test_several_beats_in_one_tick_are_not_lost():
    src = Source()
    d = Delayed(src, delay_s=0.05)
    src.beats = 4                       # counter jumped by four
    now = 100.0
    for _ in range(30):
        d.sample(now); now += 0.005
    assert d.beats == 4


def test_beat_flag_fires_once_per_new_beat():
    src = Source()
    d = Delayed(src, delay_s=0.02)
    now, flags = 100.0, 0
    for i in range(60):
        if i in (10, 30):
            src.beats += 1
        d.sample(now)
        flags += bool(d.beat)
        now += 0.005
    assert flags == 2, f"beat flag fired {flags} times for 2 beats"


def test_undelayed_fields_read_through():
    d = Delayed(Source(), delay_s=0.1)
    assert d.bpm == 128.0 and d.running is True


def test_negative_delay_is_clamped():
    """Nothing can make a live capture fire early."""
    assert Delayed(Source(), delay_s=-0.5).delay_s == 0.0


def test_snapshot_reports_the_delay():
    snap = Delayed(Source(), delay_s=0.25).snapshot()
    assert snap["delay_ms"] == 250 and snap["bpm"] == 128.0


def test_lifecycle_delegates_to_the_source():
    src = Source()
    d = Delayed(src, delay_s=0.1)
    d.stop()
    assert src.stopped == 1


def test_history_does_not_grow_without_bound():
    src = Source()
    d = Delayed(src, delay_s=0.1)
    now = 100.0
    for _ in range(2000):
        d.sample(now); now += 0.005
    # Only the delay window is retained.
    assert len(d._history) <= 0.1 / 0.005 + 5, len(d._history)


def test_a_source_that_cannot_start_does_not_break_the_effect():
    """No sound card must degrade to a fixed tempo, not a 500."""
    import asyncio

    class Broken(Source):
        def start(self): raise RuntimeError("sounddevice not installed")

    d = Delayed(Broken(), delay_s=0.2)
    asyncio.run(d.start())      # must not raise
    d.stop()
