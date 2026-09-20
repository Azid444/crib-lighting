# crib-lighting

One app for every light in my room. No cloud, no accounts, no vendor apps.
A small service runs on my PC, talks to each light on its own protocol, and
puts them all behind one web app on my iPhone — plus a rave mode that listens
to whatever the PC is playing.

| Light | Protocol | Notes |
|---|---|---|
| MR-Star TV backlight | Bluetooth LE | PC needs Bluetooth and to be in the room |
| WLED strip | WiFi, JSON API | fully local, nothing to set up |
| Tuya wall switch | WiFi, local protocol | one-time local-key extraction |

## Why a PC runs this

An iPhone on its own cannot control these three. Safari has never supported
Web Bluetooth, so no web app can reach the MR-Star, and browsers cannot open
the raw TCP socket the Tuya local protocol needs. Only WLED is reachable from
a browser.

So something has to sit between the phone and the lights. This runs on the PC
you already own — which also means rave mode can listen to your sound card,
something a headless box could not do as well.

**The tradeoff:** when the PC is off, the phone cannot control the MR-Star or
the wall switch. The Tuya unit is a real wall switch, so the physical button
always works regardless — you are never locked out of your own lights.

The only way to avoid an always-on machine entirely is a native iOS app
(CoreBluetooth can do BLE, and raw sockets can do Tuya). That needs a Mac,
Xcode, and re-signing every 7 days on a free Apple account.

## Setup

```bash
git clone <this repo> && cd crib-lighting
python3 -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Then fill in `config.yaml`:

**WLED strip** — just its IP. Set a DHCP reservation in your router so it
never moves.

**MR-Star** — find its MAC address:

```bash
python -m crib.scan
```

Look for `MRSTAR`, `LEDnetWF`, `Triones`, or `LEDBLE`. These boards ship with
one of two protocols; if the default (`triones`) does nothing, add
`protocol: lednet` to that device in `config.yaml`.

**Tuya wall switch** — a one-time key extraction, after which everything is
local and the Tuya cloud is never contacted again:

```bash
python -m tinytuya wizard
```

That gives you the `device_id`, `local_key`, and IP.

Run it:

```bash
python -m crib.app
```

## On your iPhone

Open `http://<pc-ip>:8080`, then **Share → Add to Home Screen**. It launches
fullscreen with its own icon — it behaves like a native app without ever
going near the App Store.

Find your PC's IP with `ipconfig` (Windows) or `ip addr` (Linux/macOS). Both
devices must be on the same WiFi. If the page will not load, your PC firewall
is almost certainly blocking port 8080.

## Sound-reactive rave

The **🎵 SOUND REACTIVE** button drives every light from what is actually
playing: bass sets brightness, each detected kick slams a new colour, and
treble transients flash white. The UI shows detected BPM and a live level
meter.

Both the kick and treble detectors are **relative** — they compare against the
track's own recent average, so they work at any volume rather than needing a
threshold tuned per song.

By default it listens to your **microphone**. To react to what the PC is
*playing* instead, you need a loopback device:

- **Windows** — WASAPI loopback is built in. Run `python -m crib.audio` and
  look for a "Stereo Mix" or loopback entry; if there is none, install
  [VB-CABLE](https://vb-audio.com/Cable/).
- **macOS** — install [BlackHole](https://existential.audio/blackhole/) and
  create a Multi-Output Device so you can still hear the audio.
- **Linux** — use the PulseAudio/PipeWire `.monitor` source for your output.

Then list inputs and set the index in `config.yaml`:

```bash
python -m crib.audio
```

If no audio device is available, sound mode **falls back to a fixed 128 BPM
rave** rather than failing — the button always does something.

## Effects and scenes

`rave` (fixed tempo), `sound` (audio-reactive), `strobe`, `rainbow`,
`breathe`, `solid`, and the scenes `bright`, `chill`, `movie`, `off`.

### Why the ceiling light never strobes

The Tuya wall switch is a **mechanical relay**. Rapid switching would destroy
it within minutes and sound like a machine gun. Drivers declare what they can
physically do via `Caps`, and the effect engine reads those flags: during any
strobing effect the ceiling light is simply switched off, which is what you
want during a rave anyway. This is enforced in code, not left to the UI.

## Architecture

```
crib/
  drivers/     one class per protocol behind a common Light interface
    base.py    Light + Caps + per-device rate limiting
  audio.py     FFT bands, adaptive beat/transient detection
  effects.py   frame loop at 20fps; effects return a target per light
  room.py      owns the lights, the engine, the audio, the scenes
  app.py       local REST + WebSocket API, serves the phone UI
  web/         single-page UI, installable to the iPhone home screen
```

Each driver declares a safe command rate and `Light.set()` throttles to it,
dropping frames rather than queueing a backlog a device can never drain. A
device that fails mid-effect is marked offline and the loop carries on, so an
unplugged strip never stops the rave.

Audio blocks arrive faster than the 20fps effect loop reads them, so beats and
transients are exposed as **monotonic counters** rather than momentary flags —
a slower consumer compares against the last value it saw and cannot miss an
event.

## API

```
GET    /api/state           lights, caps, running effect, audio levels
POST   /api/light/{id}      {"on":true,"brightness":200,"color":[255,0,0]}
POST   /api/scene/{name}    bright | chill | movie | off
POST   /api/effect          {"name":"sound"} or {"name":"rave","bpm":128}
DELETE /api/effect          stop
GET    /api/audio/devices   input devices, to find your loopback
WS     /ws                  state pushed to every open phone
```

## Tests

```bash
pip install pytest pytest-asyncio && python -m pytest
```

47 tests, no hardware and no sound card required. Fake devices cover the
engine and API, the MR-Star packet encoding is asserted byte by byte, and the
beat detector is verified against synthesised tracks at known tempos.

## Security

There is no authentication. This is designed for your home LAN only — do not
port-forward it to the internet.
