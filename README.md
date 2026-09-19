# crib-lighting

One app for every light in my room. No cloud, no accounts, no vendor apps —
a small service on a Raspberry Pi in the room talks to each light on its own
protocol and puts them all behind one web app on my iPhone.

| Light | Protocol | Notes |
|---|---|---|
| MR-Star TV backlight | Bluetooth LE | needs the Pi to be in the room (~10m range) |
| WLED strip | WiFi, JSON API | fully local, no setup needed |
| Tuya room switch | WiFi, local protocol | needs a one-time local-key extraction |

## Hardware

A **Raspberry Pi 4 (2GB)** or **Pi Zero 2 W** — both have WiFi and Bluetooth
LE built in, which is the whole requirement. Plus a microSD card and a power
supply. It has to physically live in the room, because Bluetooth will not
reach through walls.

## Setup

```bash
git clone <this repo> && cd crib-lighting
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Then fill in `config.yaml`:

**WLED strip** — just its IP address. Set a DHCP reservation in your router so
it never moves.

**MR-Star** — find its MAC:

```bash
python -m crib.scan
```

Look for `MRSTAR`, `LEDnetWF`, `Triones`, or `LEDBLE`. These boards ship with
one of two protocols; if the default (`triones`) does nothing, add
`protocol: lednet` to the device in `config.yaml`.

**Tuya switch** — one-time key extraction, after which everything is local:

```bash
pip install tinytuya && python -m tinytuya wizard
```

That gives you the `device_id`, `local_key`, and IP. Once you have them the
Tuya cloud is never contacted again.

Run it:

```bash
python -m crib.app
```

Open `http://<pi-ip>:8080` on your iPhone, then **Share → Add to Home Screen**.
It launches fullscreen with its own icon — it behaves like a native app
without ever going near the App Store.

To keep it running across reboots, edit the paths in `crib-lighting.service`
and install it (instructions are in the file).

## Effects

`rave` is the point of the project: one clock drives every light, so the strip
and the TV backlight slam the same colour on the same beat despite being on
completely different radios. Every fourth beat is a hard white strobe.
Adjustable with `bpm`.

Also: `strobe`, `rainbow`, `breathe`, `solid`, and the scenes `bright`,
`chill`, `movie`, `off`.

### About the ceiling light

The Tuya switch is a **mechanical relay**, so it cannot strobe — rapid
switching would destroy it within minutes. Drivers declare what they can
physically do via `Caps`, and the effect engine reads those flags: during
rave and strobe the ceiling light is simply switched off, which is what you
want anyway. This is enforced in code, not left to the UI.

## Architecture

```
crib/
  drivers/     one class per protocol, all behind a common Light interface
    base.py    Light + Caps + per-device rate limiting
  effects.py   frame loop at 20fps; effects return a target per light
  room.py      owns the lights, the engine, and the scene presets
  app.py       local REST + WebSocket API, serves the phone UI
  web/         single-page UI, installable to the iPhone home screen
```

Each driver declares a safe command rate and `Light.set()` throttles to it,
dropping frames rather than queueing a backlog a device can never drain. A
device that fails mid-effect is marked offline and the loop carries on, so an
unplugged strip never stops the rave.

## API

```
GET    /api/state           everything: lights, caps, running effect
POST   /api/light/{id}      {"on":true,"brightness":200,"color":[255,0,0]}
POST   /api/scene/{name}    bright | chill | movie | off
POST   /api/effect          {"name":"rave","bpm":128}
DELETE /api/effect          stop
WS     /ws                  state pushed to every open phone
```

## Tests

```bash
pip install pytest pytest-asyncio && python -m pytest
```

27 tests, no hardware required — fake devices cover the engine and API, and
the MR-Star packet encoding is asserted byte by byte.

## Security

There is no authentication. This is designed to sit on your home LAN only —
do not port-forward it to the internet.
