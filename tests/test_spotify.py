"""Spotify beat-grid timing and PKCE. No network, no credentials."""
import base64
import hashlib

import pytest

from crib.spotify import SpotifyBeats, SpotifyClient, pkce_pair


def analysis(bpm=120, seconds=30):
    step = 60.0 / bpm
    n = int(seconds / step)
    return {
        "track": {"tempo": bpm},
        "beats": [{"start": i * step} for i in range(n)],
        "bars": [{"start": i * step * 4} for i in range(n // 4)],
    }


# --- PKCE ----------------------------------------------------------------

def test_pkce_challenge_matches_verifier():
    verifier, challenge = pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    assert challenge == expected


def test_pkce_is_unpadded_and_long_enough():
    """Spotify rejects padded or short verifiers."""
    for _ in range(5):
        v, c = pkce_pair()
        assert "=" not in v and "=" not in c
        assert 43 <= len(v) <= 128


def test_pkce_pairs_are_unique():
    assert len({pkce_pair()[0] for _ in range(20)}) == 20


def test_auth_url_has_required_params(tmp_path):
    c = SpotifyClient("abc123", token_path=str(tmp_path / "t.json"))
    url = c.auth_url()
    for part in ("client_id=abc123", "code_challenge_method=S256",
                 "response_type=code", "code_challenge="):
        assert part in url
    assert "user-modify-playback-state" in url


def test_unauthorized_until_a_token_exists(tmp_path):
    assert not SpotifyClient("x", token_path=str(tmp_path / "t.json")).authorized


# --- beat grid timing -----------------------------------------------------

def test_beats_fire_at_the_right_times():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=120))   # a beat every 0.5s
    b.sync(progress_s=0.0, playing=True, now=100.0)

    now, fired = 100.0, []
    while now < 104.0:
        before = b.beats
        b.advance(now)
        if b.beats > before:
            fired.append(now)
        now += 0.01

    assert 7 <= len(fired) <= 9, f"{len(fired)} beats in 4s at 120bpm"
    gaps = [round(y - x, 2) for x, y in zip(fired, fired[1:])]
    assert all(abs(g - 0.5) < 0.03 for g in gaps), gaps


def test_no_beats_while_paused():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis())
    b.sync(progress_s=0.0, playing=False, now=100.0)
    for i in range(300):
        b.advance(100.0 + i * 0.01)
    assert b.beats == 0 and b.bass == 0.0


def test_seeking_forward_does_not_dump_every_skipped_beat():
    """Without re-seeking the grid, a skip would fire hundreds of beats at once."""
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=120, seconds=300))
    b.sync(progress_s=0.0, playing=True, now=100.0)
    b.advance(100.0)
    b.sync(progress_s=120.0, playing=True, now=101.0)  # user scrubs ahead
    before = b.beats
    b.advance(101.0)
    assert b.beats - before <= 1, "flooded with skipped beats"


def test_seeking_backward_replays_beats():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=120))
    b.sync(progress_s=10.0, playing=True, now=100.0)
    b.advance(100.0)
    b.sync(progress_s=0.0, playing=True, now=101.0)
    before = b.beats
    for i in range(120):
        b.advance(101.0 + i * 0.01)
    assert b.beats > before, "grid did not rewind"


def test_position_extrapolates_between_polls():
    b = SpotifyBeats()
    b.sync(progress_s=5.0, playing=True, now=100.0)
    assert b.position(100.5) == pytest.approx(5.5)
    b.sync(progress_s=5.0, playing=False, now=100.0)
    assert b.position(100.5) == pytest.approx(5.0)


def test_bars_drive_the_flash_counter():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=120))  # a bar every 2s
    b.sync(progress_s=0.0, playing=True, now=100.0)
    for i in range(900):
        b.advance(100.0 + i * 0.01)
    assert 3 <= b.spikes <= 6, f"{b.spikes} bars in 9s"
    assert b.beats > b.spikes, "bars should be rarer than beats"


def test_bass_decays_between_beats():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=60))  # a beat every second
    b.sync(progress_s=0.0, playing=True, now=100.0)
    b.advance(100.0)
    peak = b.bass
    for i in range(1, 40):
        b.advance(100.0 + i * 0.01)
    assert 0 < b.bass < peak, "envelope did not decay"


def test_missing_analysis_is_survivable():
    """A deprecated-endpoint 403 gives us no grid; nothing may crash."""
    b = SpotifyBeats()
    b.set_analysis("t1", None)
    b.sync(progress_s=0.0, playing=True, now=100.0)
    for i in range(200):
        b.advance(100.0 + i * 0.01)
    assert b.beats == 0 and b.has_grid is False


def test_snapshot_is_json_safe():
    import json
    b = SpotifyBeats()
    b.set_analysis("t1", analysis())
    json.dumps(b.snapshot())


def test_track_change_resets_the_grid():
    b = SpotifyBeats()
    b.set_analysis("t1", analysis(bpm=120))
    b.sync(progress_s=20.0, playing=True, now=100.0)
    b.advance(100.0)
    b.set_analysis("t2", analysis(bpm=174))
    assert b._beat_i == 0 and b.bpm == 174
