# crib-lighting

One app for every light in my room, synced to whatever Spotify is playing.
A small service runs on my PC, talks to each light on its own protocol, and
puts them all behind one web app on my iPhone — with a rave mode that follows
the music and never moves playback off the device it is already on.

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

Paste this into PowerShell:

```powershell
irm https://raw.githubusercontent.com/Azid444/crib-lighting/claude/room-lighting-unification-a1i9fy/bootstrap.ps1 | iex
```

That is the whole thing. It downloads the project, installs Python if you do
not have it, installs the dependencies, opens the firewall, sets it to start
at login, launches it, and opens a browser.

Already downloaded it? Right-click **`setup.ps1`** → **Run with PowerShell**
instead. Use **Run as Administrator** if you can — that lets it open the
firewall port, which is the usual reason a phone cannot connect.

In the browser that opens, press **Scan for my lights**. It sweeps the network
and Bluetooth, finds your devices, and writes `config.yaml` for you. Takes
about fifteen seconds; have the lights powered on.

You never run any of this again — it starts by itself from then on.

### The one thing it cannot find for you

Tuya wall switches are discovered on the network, but their **local key** is
never broadcast — it only exists in your Tuya account. The wizard shows a box
for it and tells you where to get it:

```powershell
.venv\Scripts\python.exe -m tinytuya wizard
```

Leave it blank if you would rather not bother. Your strip and TV backlight
still work, and the wall switch carries on being a normal wall switch.

Spotify is the same idea: paste a Client ID into the wizard if you want
now-playing and controls on your phone, or skip it.

### Changing things later

Everything worth tweaking — beat source, sensitivity, rave tempo, sync delay —
is on the phone. To add a light you bought since, open `/setup` and scan
again; your existing tuning is kept.

## On your iPhone

Open `http://<pc-ip>:8080`, then **Share → Add to Home Screen**. It launches
fullscreen with its own icon — it behaves like a native app without ever
going near the App Store.

Setup prints the address, and `start.bat` prints it again any time. Both
devices must be on the same WiFi.

If the page will not load, it is almost always the Windows firewall. Re-run
`setup_windows.ps1` as Administrator, or add the rule yourself:

```powershell
New-NetFirewallRule -DisplayName "crib-lighting" -Direction Inbound `
    -LocalPort 8080 -Protocol TCP -Action Allow -Profile Private
```

Set a DHCP reservation for your PC in your router so the address never
changes.

## Sound-reactive rave

The **🎵 SOUND REACTIVE** button drives every light from what is actually
playing: bass sets brightness, each detected kick slams a new colour, and
treble transients flash white. The UI shows detected BPM and a live level
meter.

Both the kick and treble detectors are **relative** — they compare against the
track's own recent average, so they work at any volume rather than needing a
threshold tuned per song.

## Spotify

Set `spotify.client_id` in `config.yaml` (create an app at
[developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
add `http://127.0.0.1:8080/api/spotify/callback` as a redirect URI — no client
secret needed, it uses PKCE). Then, **in a browser on the PC**, open:

```
http://127.0.0.1:8080/api/spotify/login
```

That has to happen on the PC, not the phone: Spotify only allows plain-http
redirects to loopback addresses, so the callback cannot land on your phone.
It is a one-time step — the refresh token is saved and renewed automatically.

After that the phone shows the current track and art, and the ⏮ ⏯ ⏭ buttons
control it.

### Playback stays where it is

Spotify Connect *moves* playback between devices. This never does that. It
only reads playback state and sends transport commands, so if the Apple TV
app is playing, the sound stays on your TV and the PC is never made the
active device.

### Why Spotify alone cannot drive the beat

Beat-accurate sync needs Spotify's `/audio-analysis` endpoint, which gives the
exact timestamp of every beat and bar. Spotify **deprecated it for apps
created after 2024-11-27** — new client IDs get a 403. Apps that already had
access kept it.

So the app asks once and adapts:

- **Access granted** → beats come straight from the grid, sample-accurate, and
  no audio capture is needed at all. `python -m crib.app` logs which it got,
  and the phone shows "Synced to Spotify's beat grid".
- **403** → Spotify still gives you track info and controls, and beats come
  from the sound card instead.

Either way the lights work. The `Auto` / `Mic` / `Spotify` selector on the
phone lets you force one.

## Playing through a Bluetooth soundbar

If you pair the soundbar to the PC and play Spotify **from the PC**, loopback
capture works with no cable and no Apple TV involved. Two things to know.

**Bluetooth audio arrives late.** A2DP buffers 100-250ms. Loopback taps the
audio *before* that hop, so uncompensated the lights fire up to a quarter of a
second ahead of what you hear. Raise **Sync delay** on the phone until they
line up — 150-200ms is typical. It is live, so you can slide it while a track
is playing and watch it lock in.

Nothing can make a live capture fire *early*, which is why the control only
ever delays. Wired setups leave it at 0.

**One Bluetooth radio, two jobs.** The PC would be streaming A2DP to the
soundbar *and* driving the MR-Star over BLE at the same time. A2DP is
bandwidth-hungry and the two can contend, showing up as stuttering on the TV
backlight during loud passages. If that happens, a second USB Bluetooth
dongle (~£10) separates them — or move the MR-Star onto its own adapter.

Note this only helps if the music plays **from the PC**. Bluetooth audio flows
source → sink, so pairing the soundbar to the PC does not let the PC hear what
an Apple TV is sending.

## Getting Apple TV audio to the PC

If Spotify's grid is not available to your account, the beats have to come
from sound the PC can actually hear. Your Apple TV plays to the TV, so tap a
copy of it:

| Route | Cost | Notes |
|---|---|---|
| TV or soundbar **line-out / headphone** → PC line-in | ~£8 cable | Best option. Add a USB audio adapter (~£10) if the PC has no input jack |
| TV **optical out** → USB capture with optical in | ~£15 | If the TV has no analogue out |
| **Microphone** | free | Set `loopback: false`. Works well when it is loud; picks up talking |

None of these change what the Apple TV is doing — you are only tapping a copy
of the signal. Set the captured device with `python -m crib.audio` and put its
index in `config.yaml`.

If the music is coming *from the PC* instead, none of this is needed — see
below.

### Hearing what the PC plays

On Windows this needs **no extra software**. Windows exposes WASAPI loopback,
which captures an output device directly, so leaving `device: null` in
`config.yaml` makes it listen to your speakers automatically — no Stereo Mix,
no VB-CABLE. Nothing changes about how your audio sounds.

To pick a specific device instead:

```powershell
.venv\Scripts\python.exe -m crib.audio
```

That lists every input plus every loopback-capable output; put the index in
`config.yaml`. Set `loopback: false` to use the microphone instead, which
reacts to the room rather than the PC — better if the music is coming from a
phone or a speaker rather than the PC itself.

If no device can be opened at all, sound mode **falls back to a fixed 128 BPM
rave** rather than failing, so the button always does something.

`sensitivity` tunes the beat detector: lower catches more beats, higher only
the big hits. 1.35 is a reasonable default.

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
  discover.py  finds lights over WiFi, BLE and Tuya broadcast
  setup.py     writes config.yaml from what was discovered
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
POST   /api/source          {"source":"auto"|"mic"|"spotify"}
POST   /api/settings        {"sensitivity":1.4,"bpm":174,"delay_ms":180}
GET    /api/audio/devices   input devices, to find your loopback
GET    /api/spotify/login   one-time authorisation (open on the PC)
POST   /api/spotify/{cmd}   play | pause | next | previous
WS     /ws                  state pushed to every open phone
```

## Tests

```bash
pip install pytest pytest-asyncio && python -m pytest
```

125 tests, no hardware, sound card, or Spotify account required. Fake devices cover the
engine and API, the MR-Star packet encoding is asserted byte by byte, and the
beat detector is verified against synthesised tracks at known tempos. The
WASAPI loopback selection is tested against a simulated Windows device tree,
since it cannot run on Linux. Spotify's beat-grid timing is tested against a
synthetic analysis, including seeking, pausing and track changes. Sync delay
is measured end to end: 10ms baseline, 229ms with a 200ms offset set. The
first-run wizard is covered over HTTP, from empty folder to running lights.

## Troubleshooting

**Phone cannot load the page.** Windows firewall, nearly every time — see
above. Check both devices are on the same WiFi, and that your network is set
to *Private* rather than *Public* in Windows settings, since the firewall rule
only covers private networks.

**The scan finds nothing.** Lights must be powered on and the PC on the same
WiFi as them. Bluetooth devices must not be paired in Windows settings.

**MR-Star never connects.** `bleak` uses the Windows WinRT Bluetooth stack,
which needs Windows 10 or newer and a BLE-capable adapter (most built-in WiFi
cards have one; a £10 USB dongle fixes an older desktop). Make sure the device
is **not paired** in Windows Bluetooth settings — these controllers are not
meant to be paired and Windows holding the connection stops this from opening
it. If `python -m crib.scan` finds nothing, the adapter is the problem; if it
finds the device but colours do nothing, you have the other protocol, so set
`protocol: lednet` on it.

**Tuya switch stops responding after a while.** Tuya devices drop idle
connections. The driver keeps a persistent socket and reconnects, but if the
device changed IP, update it — a DHCP reservation prevents this.

**Spotify says "not connected" on the phone.** The authorisation has to be
done in a browser on the PC, at `http://127.0.0.1:8080/api/spotify/login`.
Check the redirect URI registered on your Spotify app matches
`config.yaml` exactly, including the port.

**Spotify shows the track but the lights ignore the beat.** Your app does not
have `/audio-analysis` access (see above). Capture the audio instead — the
lights still react, just from sound rather than the grid.

**Sound mode says "no audio input".** Windows privacy settings can block
microphone access; loopback capture is unaffected, so leave `device: null` to
use it. Check nothing else has the device open in exclusive mode.

**Lights run ahead of the music.** Speaker lag. Raise **Sync delay** on the
phone until they match — Bluetooth usually needs 150-200ms.

**Lights lag during rave.** Bluetooth is the bottleneck. The MR-Star is capped
at 10 commands/sec by design — pushing harder fills its buffer and makes it
stutter rather than go faster. If you are *also* streaming audio over
Bluetooth from the same PC, the two are competing for one radio; a second USB
dongle fixes it.

## Security

There is no authentication. This is designed for your home LAN only — do not
port-forward it to the internet.
