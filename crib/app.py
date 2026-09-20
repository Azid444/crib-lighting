"""Local HTTP + WebSocket API and the phone UI. Nothing leaves your LAN."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .effects import EFFECTS
from .room import Room

log = logging.getLogger(__name__)
CONFIG = os.environ.get("CRIB_CONFIG", "config.yaml")
WEB = os.path.join(os.path.dirname(__file__), "web")

room: Room | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global room
    room = Room.from_file(CONFIG)
    await room.connect_all()
    await room.start_spotify()
    yield
    await room.disconnect_all()


app = FastAPI(title="crib-lighting", lifespan=lifespan)


def get_room() -> Room:
    if room is None:
        raise HTTPException(503, "room not ready")
    return room


class LightBody(BaseModel):
    on: bool | None = None
    brightness: int | None = Field(None, ge=0, le=255)
    color: tuple[int, int, int] | None = None


class EffectBody(BaseModel):
    name: str
    bpm: float | None = Field(None, gt=20, le=300)
    hz: float | None = Field(None, gt=0, le=25)
    speed: float | None = None
    color: tuple[int, int, int] | None = None
    brightness: int | None = Field(None, ge=0, le=255)


@app.get("/api/state")
async def state() -> dict:
    return get_room().snapshot()


@app.post("/api/light/{light_id}")
async def set_light(light_id: str, body: LightBody) -> dict:
    r = get_room()
    if light_id not in r.lights:
        raise HTTPException(404, f"no light {light_id!r}")
    # A manual touch means the user is taking over from the effect.
    await r.engine.stop()
    await r.lights[light_id].set(
        throttle=False, **body.model_dump(exclude_none=True)
    )
    return r.snapshot()


@app.post("/api/scene/{name}")
async def scene(name: str) -> dict:
    r = get_room()
    try:
        await r.apply_scene(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return r.snapshot()


# -- first-run setup -------------------------------------------------------
# Held between the scan and the save so the user can fill in a Tuya key
# without us having to scan the network again.
_found: list[dict] = []


@app.get("/api/setup/state")
async def setup_state() -> dict:
    r = get_room()
    return {
        "needs_setup": r.needs_setup,
        "configured": [l.snapshot() for l in r.lights.values()],
        "found": _found,
        "spotify_configured": r.spotify is not None,
    }


@app.post("/api/setup/scan")
async def setup_scan() -> dict:
    """Look for lights on the network and over Bluetooth."""
    from .discover import discover_all

    global _found
    results = await discover_all(timeout=10.0)
    _found = [d for key in ("wled", "mrstar", "tuya") for d in results[key]]
    return {
        "found": _found,
        # Unidentified Bluetooth devices, for the user to pick from when
        # nothing advertised itself as a light.
        "candidates": results["candidates"],
        "notes": results["notes"],
        "counts": {k: len(results[k]) for k in ("wled", "mrstar", "tuya")},
    }


class SaveBody(BaseModel):
    devices: list[dict] = Field(default_factory=list)
    spotify_client_id: str = ""


@app.post("/api/setup/save")
async def setup_save(body: SaveBody) -> dict:
    """Write config.yaml and bring the lights up without a restart."""
    from .setup import build_config, load_config, save_config

    global room
    existing = load_config(CONFIG)
    # Keep tuning the user already did; replace only the device list.
    existing.pop("devices", None)
    config = build_config(body.devices, body.spotify_client_id, existing)
    try:
        save_config(CONFIG, config)
    except Exception as exc:
        raise HTTPException(500, f"could not write {CONFIG}: {exc}")

    if room is not None:
        await room.disconnect_all()
    room = Room(config)
    await room.connect_all()
    await room.start_spotify()
    return {"saved": True, "state": room.snapshot()}


@app.get("/setup")
async def setup_page() -> FileResponse:
    return FileResponse(os.path.join(WEB, "setup.html"))


@app.get("/api/audio/devices")
async def audio_devices() -> dict:
    """Input devices, so you can pick the loopback from the phone."""
    from .audio import list_devices

    try:
        return {"devices": list_devices()}
    except Exception as exc:
        raise HTTPException(503, f"audio unavailable: {exc}")


@app.get("/api/effects")
async def list_effects() -> dict:
    return {"effects": sorted(EFFECTS)}


@app.post("/api/effect")
async def effect(body: EffectBody) -> dict:
    r = get_room()
    opts = body.model_dump(exclude_none=True)
    name = opts.pop("name")
    try:
        await r.start_effect(name, **opts)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return r.snapshot()


@app.delete("/api/effect")
async def stop_effect() -> dict:
    r = get_room()
    await r.stop_effect()
    return r.snapshot()


class SettingsBody(BaseModel):
    sensitivity: float | None = Field(None, ge=1.02, le=3.0)
    bpm: float | None = Field(None, gt=20, le=300)
    gain: float | None = Field(None, gt=0, le=5)
    delay_ms: float | None = Field(None, ge=0, le=1000)


@app.post("/api/settings")
async def settings(body: SettingsBody) -> dict:
    r = get_room()
    values = body.model_dump(exclude_none=True)
    r.update_settings(**values)
    # A delay change has to rebuild the wrapper around the live source.
    if "delay_ms" in values and r.engine.running:
        await r.start_effect(r.engine.running)
    return r.snapshot()


class SourceBody(BaseModel):
    source: str


@app.post("/api/source")
async def set_source(body: SourceBody) -> dict:
    """Choose what drives the beat: auto, mic or spotify."""
    r = get_room()
    try:
        r.set_source(body.source)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    # Re-bind a running effect to the newly chosen source.
    if r.engine.running:
        await r.start_effect(r.engine.running)
    return r.snapshot()


# -- Spotify ---------------------------------------------------------------
# Authorise once in a browser on the PC: Spotify only permits loopback
# redirect URIs over plain http, so the callback cannot land on the phone.

@app.get("/api/spotify/login")
async def spotify_login():
    r = get_room()
    if r.spotify is None:
        raise HTTPException(400, "no spotify.client_id in config.yaml")
    return RedirectResponse(r.spotify.auth_url())


@app.get("/api/spotify/callback")
async def spotify_callback(code: str | None = None, error: str | None = None):
    r = get_room()
    if error:
        return HTMLResponse(f"<h2>Spotify declined: {error}</h2>", status_code=400)
    if not code or r.spotify is None:
        raise HTTPException(400, "missing authorization code")
    try:
        await r.spotify.exchange(code)
    except Exception as exc:
        return HTMLResponse(f"<h2>Authorization failed</h2><p>{exc}</p>",
                            status_code=400)
    await r.start_spotify()
    return HTMLResponse(
        "<h2>Spotify connected.</h2>"
        "<p>You can close this tab and go back to your phone.</p>"
    )


@app.post("/api/spotify/{action}")
async def spotify_command(action: str) -> dict:
    """Transport control. Acts on whichever device is already playing."""
    r = get_room()
    if r.spotify is None or not r.spotify.authorized:
        raise HTTPException(400, "Spotify not connected")
    try:
        await r.spotify.command(action)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Spotify rejected {action}: {exc}")
    return r.snapshot()


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    """Pushes state to every open phone so two devices never disagree."""
    await socket.accept()
    try:
        while True:
            await socket.send_json(get_room().snapshot())
            await asyncio.sleep(1.0)
    except (WebSocketDisconnect, RuntimeError):
        pass


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
async def index():
    # Nothing configured yet means this is a first run: go straight to setup.
    if room is not None and room.needs_setup:
        return RedirectResponse("/setup")
    return FileResponse(os.path.join(WEB, "index.html"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from .setup import load_config

    # A missing config is the normal first run: start anyway and serve the
    # setup wizard, which is what writes the config in the first place.
    cfg = load_config(CONFIG).get("server") or {}
    host, port = cfg.get("host", "0.0.0.0"), int(cfg.get("port", 8080))
    if not os.path.exists(CONFIG):
        log.info("no %s yet - open http://127.0.0.1:%d/setup", CONFIG, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
