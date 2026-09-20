"""Spotify Web API: now playing, transport control, and beat sync.

Playback stays wherever it already is — an Apple TV, a phone, a speaker. This
only *reads* what is playing and sends transport commands, so nothing is ever
pulled onto the PC the way Spotify Connect would.

Beat-accurate sync needs the /audio-analysis endpoint, which Spotify
deprecated for apps created after 2024-11-27; new client IDs get a 403. We ask
once, remember the answer, and fall back to whatever the microphone or line-in
hears. Metadata and control are unaffected and always work.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import aiohttp

log = logging.getLogger(__name__)

AUTH = "https://accounts.spotify.com"
API = "https://api.spotify.com/v1"
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing"
)


def pkce_pair() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge, per RFC 7636."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class SpotifyClient:
    """Thin async client with PKCE auth and automatic token refresh."""

    def __init__(
        self,
        client_id: str,
        redirect_uri: str = "http://127.0.0.1:8080/api/spotify/callback",
        token_path: str = "spotify_token.json",
    ) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_path = token_path
        self._token: dict | None = None
        self._verifier: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        # None = not tried yet, True/False = whether this app may use it.
        self.analysis_allowed: bool | None = None
        self._load()

    # -- token storage -----------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.token_path) as fh:
                self._token = json.load(fh)
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("could not read %s: %s", self.token_path, exc)

    def _save(self) -> None:
        try:
            with open(self.token_path, "w") as fh:
                json.dump(self._token, fh)
            if os.name == "posix":
                os.chmod(self.token_path, 0o600)
        except Exception as exc:
            log.warning("could not write %s: %s", self.token_path, exc)

    @property
    def authorized(self) -> bool:
        return bool(self._token and self._token.get("refresh_token"))

    # -- auth --------------------------------------------------------------
    def auth_url(self) -> str:
        self._verifier, challenge = pkce_pair()
        return f"{AUTH}/authorize?" + urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": SCOPES,
        })

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10.0)
            )
        return self._session

    async def exchange(self, code: str) -> None:
        if not self._verifier:
            raise RuntimeError("no auth in progress; start at /api/spotify/login")
        await self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": self._verifier,
        })
        self._verifier = None

    async def _refresh(self) -> None:
        if not (self._token and self._token.get("refresh_token")):
            raise RuntimeError("not authorized with Spotify")
        await self._token_request({
            "grant_type": "refresh_token",
            "refresh_token": self._token["refresh_token"],
        })

    async def _token_request(self, data: dict) -> None:
        session = await self._session_get()
        data = {**data, "client_id": self.client_id}
        async with session.post(f"{AUTH}/api/token", data=data) as r:
            body = await r.json()
            if r.status != 200:
                raise RuntimeError(f"Spotify auth failed: {body}")
        # A refresh response may omit refresh_token; keep the one we have.
        old = (self._token or {}).get("refresh_token")
        body.setdefault("refresh_token", old)
        body["expires_at"] = time.time() + float(body.get("expires_in", 3600)) - 30
        self._token = body
        self._save()

    async def _access_token(self) -> str:
        async with self._lock:
            if not self._token:
                raise RuntimeError("not authorized with Spotify")
            if time.time() >= self._token.get("expires_at", 0):
                await self._refresh()
            return self._token["access_token"]

    # -- api ---------------------------------------------------------------
    async def _call(self, method: str, path: str, **kw):
        session = await self._session_get()
        headers = {"Authorization": f"Bearer {await self._access_token()}"}
        async with session.request(
            method, f"{API}{path}", headers=headers, **kw
        ) as r:
            if r.status == 204:      # nothing playing / command accepted
                return None
            if r.status == 401:      # token rejected: refresh once and retry
                await self._refresh()
                headers = {"Authorization": f"Bearer {await self._access_token()}"}
                async with session.request(
                    method, f"{API}{path}", headers=headers, **kw
                ) as r2:
                    return None if r2.status == 204 else await r2.json()
            if r.status == 403:
                raise PermissionError(f"{path} forbidden")
            if r.status == 429:
                raise RuntimeError("rate limited by Spotify")
            r.raise_for_status()
            return await r.json()

    async def now_playing(self) -> dict | None:
        """Current playback, whichever device it is on."""
        data = await self._call("GET", "/me/player")
        if not data or not data.get("item"):
            return None
        item = data["item"]
        images = (item.get("album") or {}).get("images") or []
        return {
            "track_id": item.get("id"),
            "title": item.get("name"),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "art": images[-1]["url"] if images else None,
            "duration_s": item.get("duration_ms", 0) / 1000.0,
            "progress_s": (data.get("progress_ms") or 0) / 1000.0,
            "playing": bool(data.get("is_playing")),
            "device": (data.get("device") or {}).get("name"),
        }

    async def command(self, action: str) -> None:
        if action in ("play", "pause"):
            await self._call("PUT", f"/me/player/{action}")
        elif action in ("next", "previous"):
            await self._call("POST", f"/me/player/{action}")
        else:
            raise ValueError(f"unknown action {action!r}")

    async def audio_analysis(self, track_id: str) -> dict | None:
        """Beat grid for a track, or None if this app may not have it."""
        if self.analysis_allowed is False:
            return None
        try:
            data = await self._call("GET", f"/audio-analysis/{track_id}")
        except PermissionError:
            # Deprecated for apps created after 2024-11-27. Ask only once.
            self.analysis_allowed = False
            log.info("Spotify audio-analysis unavailable; using live audio instead")
            return None
        except Exception as exc:
            log.warning("audio-analysis failed: %s", exc)
            return None
        self.analysis_allowed = True
        return data

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class SpotifyBeats:
    """Turns Spotify's beat grid into the same signals the mic analyser emits.

    Exposing `beats`/`spikes` counters and band levels means the sound effect
    does not care which source it is driving from.

    Playback position is anchored on each poll and extrapolated between them,
    so beats land on time without polling Spotify at audio rates.
    """

    def __init__(self, client: "SpotifyClient | None" = None) -> None:
        self.client = client
        self.beats = 0
        self.spikes = 0
        self.beat = False
        self.spike = False
        self.bass = self.mid = self.treble = self.level = 0.0
        self.bpm = 0.0
        self.running = False
        self.error: str | None = None
        self.source: str | None = None
        self.track: dict | None = None

        self._beat_times: list[float] = []
        self._bar_times: list[float] = []
        self._beat_i = 0
        self._bar_i = 0
        self._track_id: str | None = None
        self._anchor = (0.0, 0.0)   # (position_s, monotonic when measured)
        self._playing = False
        self._task: asyncio.Task | None = None
        self._last_advance = 0.0

    # -- pure timing logic (unit tested without any network) ---------------
    def set_analysis(self, track_id: str, analysis: dict | None) -> None:
        self._track_id = track_id
        self._beat_times = [b["start"] for b in (analysis or {}).get("beats", [])]
        self._bar_times = [b["start"] for b in (analysis or {}).get("bars", [])]
        self.bpm = float((analysis or {}).get("track", {}).get("tempo", 0.0) or 0.0)
        self._beat_i = self._bar_i = 0

    def sync(self, progress_s: float, playing: bool, now: float) -> None:
        """Re-anchor playback position from a fresh poll."""
        previous = self.position(now)
        self._anchor = (progress_s, now)
        self._playing = playing
        # A seek (or track change) means the grid pointers are stale.
        if abs(progress_s - previous) > 1.0:
            self._reseek(progress_s)

    def _reseek(self, position: float) -> None:
        import bisect

        self._beat_i = bisect.bisect_left(self._beat_times, position)
        self._bar_i = bisect.bisect_left(self._bar_times, position)

    def position(self, now: float) -> float:
        pos, at = self._anchor
        return pos + (now - at if self._playing else 0.0)

    def advance(self, now: float) -> None:
        """Fire any beats whose time has arrived, and decay the envelopes."""
        dt = max(0.0, now - self._last_advance) if self._last_advance else 0.02
        self._last_advance = now

        self.beat = self.spike = False
        if not self._playing:
            self.bass = self.mid = self.treble = self.level = 0.0
            return

        pos = self.position(now)
        while self._beat_i < len(self._beat_times) and self._beat_times[self._beat_i] <= pos:
            self._beat_i += 1
            self.beats += 1
            self.beat = True
            self.bass = 0.09          # what the mic would read on a kick
        while self._bar_i < len(self._bar_times) and self._bar_times[self._bar_i] <= pos:
            self._bar_i += 1
            self.spikes += 1          # flash on the downbeat of each bar
            self.spike = True

        # Same attack/release feel as the live analyser.
        decay = 0.82 ** (dt / 0.023)
        self.bass *= decay
        self.mid = self.treble = self.bass * 0.5
        self.level = self.bass

    @property
    def has_grid(self) -> bool:
        return bool(self._beat_times)

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "error": self.error,
            "source": self.source,
            "bpm": round(self.bpm, 1),
            "bass": round(self.bass, 4),
            "mid": round(self.mid, 4),
            "treble": round(self.treble, 4),
            "level": round(self.level, 4),
            "beats": self.beats,
            "spikes": self.spikes,
            "track": self.track,
            "has_grid": self.has_grid,
        }

    # -- polling loop ------------------------------------------------------
    async def start(self) -> None:
        if self.running or self.client is None:
            return
        self.running = True
        self.source = "spotify"
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self.running = False
        self.source = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last_poll = 0.0
        try:
            while True:
                now = loop.time()
                if now - last_poll >= 1.0:
                    last_poll = now
                    await self._poll(now)
                self.advance(loop.time())
                # Fine enough that a beat never lands more than 10ms late.
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("spotify sync loop crashed")
            self.running = False

    async def _poll(self, now: float) -> None:
        try:
            playing = await self.client.now_playing()
        except Exception as exc:
            self.error = str(exc)
            return
        self.error = None
        if playing is None:
            self._playing = False
            self.track = None
            return

        self.track = {k: playing[k] for k in
                      ("title", "artist", "art", "playing", "device", "duration_s")}
        if playing["track_id"] != self._track_id:
            analysis = await self.client.audio_analysis(playing["track_id"])
            self.set_analysis(playing["track_id"], analysis)
        self.sync(playing["progress_s"], playing["playing"], now)
