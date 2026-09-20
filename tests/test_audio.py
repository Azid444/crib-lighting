"""Beat detection, verified against synthesised audio. No sound card needed."""
import numpy as np
import pytest

from crib.audio import Audio


def kick_track(bpm=120, seconds=8, sr=44100, block=1024):
    """A bass thump on every beat over quiet noise, as blocks."""
    n = int(sr * seconds)
    t = np.arange(n) / sr
    audio = np.random.default_rng(0).normal(0, 0.01, n)  # room noise
    beat_len = 60.0 / bpm
    for i in range(int(seconds / beat_len)):
        start = int(i * beat_len * sr)
        dur = int(0.12 * sr)
        end = min(start + dur, n)
        env = np.exp(-np.linspace(0, 8, end - start))     # percussive decay
        audio[start:end] += 0.9 * np.sin(2*np.pi*60*t[start:end]) * env
    return [audio[i:i+block] for i in range(0, n - block, block)], beat_len


def run(audio_obj, blocks, sr=44100, block=1024):
    """Feed blocks with a synthetic clock so timing is deterministic."""
    beats, now = [], 0.0
    for b in blocks:
        if audio_obj.feed(b, now=now):
            beats.append(now)
        now += block / sr
    return beats


def test_detects_beats_at_the_right_tempo():
    blocks, beat_len = kick_track(bpm=120)
    a = Audio()
    beats = run(a, blocks)
    assert len(beats) >= 10, f"only found {len(beats)} beats"
    gaps = np.diff(beats)
    # Every gap should be a whole number of beats, within a block or two.
    ratios = gaps / beat_len
    assert np.allclose(ratios, np.round(ratios), atol=0.15), ratios
    assert 100 <= a.bpm <= 140, f"bpm read {a.bpm}"


def test_reports_faster_tempo_correctly():
    blocks, _ = kick_track(bpm=160)
    a = Audio()
    run(a, blocks)
    assert 140 <= a.bpm <= 180, f"bpm read {a.bpm}"


def test_silence_produces_no_beats():
    a = Audio()
    blocks = [np.zeros(1024, dtype=np.float32) for _ in range(200)]
    assert run(a, blocks) == []
    assert a.bass == 0.0


def test_steady_tone_is_not_a_beat():
    """A sustained bass note must not machine-gun the lights."""
    sr, block = 44100, 1024
    t = np.arange(sr * 4) / sr
    tone = 0.5 * np.sin(2 * np.pi * 60 * t)
    a = Audio()
    beats = run(a, [tone[i:i+block] for i in range(0, len(tone)-block, block)])
    assert len(beats) <= 2, f"steady tone fired {len(beats)} beats"


def test_refractory_period_prevents_double_triggers():
    blocks, _ = kick_track(bpm=120)
    a = Audio()
    beats = run(a, blocks)
    assert all(b - prev >= 0.18 for prev, b in zip(beats, beats[1:]))


def test_bands_separate_bass_from_treble():
    sr, block = 44100, 1024
    t = np.arange(block * 20) / sr
    a = Audio()
    run(a, [(0.5*np.sin(2*np.pi*60*t))[i:i+block] for i in range(0, len(t)-block, block)])
    assert a.bass > a.treble

    b = Audio()
    run(b, [(0.5*np.sin(2*np.pi*9000*t))[i:i+block] for i in range(0, len(t)-block, block)])
    assert b.treble > b.bass


def test_snapshot_is_json_safe():
    import json
    json.dumps(Audio().snapshot())


def test_mono_downmix_of_stereo_input():
    a = Audio()
    a.feed(np.zeros((1024, 2), dtype=np.float32))  # must not raise


# --- the sound-reactive effect ------------------------------------------

class FakeAudio:
    running = True
    beat = False
    spike = False
    beats = 0
    spikes = 0
    bass = mid = treble = level = 0.0
    bpm = 128.0
    needs_audio = True

    def start(self): self.running = True
    def stop(self): self.running = False
    def snapshot(self): return {"running": self.running, "bpm": self.bpm}


def _light(strobe=True):
    from crib.drivers.base import Caps

    class L:
        id = "x"
        caps = Caps(color=True, brightness=True, max_hz=100 if strobe else 0.5)
    return L


def test_sound_rave_protects_the_relay():
    from crib.effects import SoundRave
    fx = SoundRave()
    fx.audio = FakeAudio()
    assert fx.frame(0.0, _light(strobe=False)) == {"on": False}


def test_sound_rave_falls_back_without_audio():
    """No loopback configured must still give you a rave, not an error."""
    from crib.effects import SoundRave
    fx = SoundRave()
    fx.audio = None
    assert fx.frame(0.1, _light()) is not None
    a = FakeAudio()
    a.running = False
    fx.audio = a
    assert fx.frame(0.1, _light()) is not None


def test_bass_drives_brightness():
    from crib.effects import SoundRave
    fx, a = SoundRave(), FakeAudio()
    fx.audio = a
    a.bass = 0.0
    quiet = fx.frame(1.0, _light())["brightness"]
    a.bass = 0.08
    loud = fx.frame(1.0, _light())["brightness"]
    assert loud > quiet


def test_beat_changes_colour():
    from crib.effects import SoundRave
    fx, a = SoundRave(), FakeAudio()
    fx.audio = a
    first = fx.frame(1.0, _light())["color"]
    a.beats += 1
    second = fx.frame(1.1, _light())["color"]
    assert first != second


def test_treble_flashes_white():
    from crib.effects import SoundRave
    fx, a = SoundRave(), FakeAudio()
    fx.audio = a
    a.spikes += 1
    assert fx.frame(1.0, _light())["color"] == (255, 255, 255)


def test_brightness_stays_in_range_under_extreme_input():
    from crib.effects import SoundRave
    fx, a = SoundRave(), FakeAudio()
    fx.audio = a
    for bass in (0.0, 0.5, 5.0, 1e6):
        a.bass = bass
        assert 0 <= fx.frame(1.0, _light())["brightness"] <= 255


def test_treble_spike_is_relative_not_absolute():
    """A hi-hat pattern must register at quiet AND loud volumes alike."""
    sr, block = 44100, 1024
    for volume in (0.02, 0.9):
        a = Audio()
        blocks, now, spikes = [], 0.0, 0
        for i in range(160):
            t = np.arange(block) / sr
            # A short burst of high-frequency content every 8th block.
            if i % 8 == 0:
                b = volume * np.sin(2 * np.pi * 9000 * t)
            else:
                b = np.random.default_rng(i).normal(0, volume * 0.02, block)
            a.feed(b, now=now)
            now += block / sr
            spikes += bool(a.spike)
        assert spikes >= 5, f"volume {volume}: only {spikes} spikes"


def test_no_spikes_on_steady_hiss():
    a = Audio()
    rng = np.random.default_rng(1)
    now, spikes = 0.0, 0
    for _ in range(160):
        a.feed(rng.normal(0, 0.05, 1024), now=now)
        now += 1024 / 44100
        spikes += bool(a.spike)
    assert spikes <= 8, f"steady hiss fired {spikes} spikes"


def test_counters_survive_a_slow_consumer():
    """The effect loop reads slower than audio arrives; no event may be lost."""
    a = Audio()
    blocks, _ = kick_track(bpm=128, seconds=6)
    now = 0.0
    for b in blocks:
        a.feed(b, now=now)
        now += 1024 / 44100
    assert a.beats >= 10, f"counted only {a.beats} beats"
