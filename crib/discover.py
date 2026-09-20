"""Finds the lights on your network so nothing has to be typed in by hand.

Each finder is independent and failure-isolated: a missing Bluetooth adapter
must not stop the WiFi scan, and vice versa. Everything returns plain dicts
shaped like `config.yaml` device entries, ready to write straight out.
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct

log = logging.getLogger(__name__)

# Tuya's broadcast key is a fixed, published constant -- md5("yGAdlopoPVldABfn").
TUYA_UDP_KEY = bytes.fromhex("6c1ec8e2bb9bb59ab50b0daf649b410a")
TUYA_PORTS = (6666, 6667)

# Names these rebadged BLE controllers advertise under. The list is a hint,
# never a gate: the boards are white-labelled and turn up under all sorts of
# names, so unmatched devices are still offered to the user to pick from.
BLE_NAMES = ("mrstar", "mr-star", "lednetwf", "triones", "ledble", "ledblue",
             "melk", "happy lighting", "ihoment", "qhm-", "led", "rgb",
             "elk-ble", "sp1", "bledom", "govee")

# Far more reliable than a name: the GATT services these controllers expose.
BLE_SERVICES = {
    "0000ffd9-0000-1000-8000-00805f9b34fb": "triones",
    "0000ffd5-0000-1000-8000-00805f9b34fb": "triones",
    "0000ff01-0000-1000-8000-00805f9b34fb": "lednet",
    "0000fff0-0000-1000-8000-00805f9b34fb": "triones",
}


def local_ipv4() -> str | None:
    """This machine's LAN address, found without sending anything."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # never leaves the machine
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


# -- WLED ------------------------------------------------------------------
async def find_wled(timeout: float = 6.0) -> list[dict]:
    """Sweep the local /24 for anything answering WLED's info endpoint."""
    import aiohttp

    ip = local_ipv4()
    if not ip:
        log.warning("could not determine local IP; skipping WLED scan")
        return []
    prefix = ip.rsplit(".", 1)[0]

    found: list[dict] = []
    sem = asyncio.Semaphore(64)          # be kind to cheap routers

    async def probe(session, host: str) -> None:
        async with sem:
            try:
                async with session.get(f"http://{host}/json/info") as r:
                    if r.status != 200:
                        return
                    info = await r.json()
            except Exception:
                return
            # WLED always reports these; nothing else on a home LAN will.
            if "leds" not in info or "ver" not in info:
                return
            found.append({
                "driver": "wled",
                "id": "strip",
                "name": info.get("name") or "WLED Strip",
                "host": host,
                "_detail": f"WLED {info.get('ver')}, "
                           f"{info.get('leds', {}).get('count', '?')} LEDs",
            })

    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
    try:
        await asyncio.gather(*(
            probe(session, f"{prefix}.{i}") for i in range(1, 255)
        ), return_exceptions=True)
    finally:
        await session.close()
    return sorted(found, key=lambda d: d["host"])


# -- MR-Star / BLE ---------------------------------------------------------
def guess_protocol(name: str) -> str:
    """LEDnetWF boards speak a different dialect to the older Triones ones."""
    return "lednet" if "lednetwf" in (name or "").lower() else "triones"


def _ble_entry(address: str, name: str, uuids: list[str]) -> dict:
    """Score one advertisement and shape it as a config device."""
    protocol, confident = None, False
    for uuid in uuids or []:
        if uuid.lower() in BLE_SERVICES:
            protocol = BLE_SERVICES[uuid.lower()]
            confident = True
            break

    lowered = (name or "").lower()
    matched_name = any(p in lowered for p in BLE_NAMES)
    if protocol is None:
        protocol = guess_protocol(name)

    if confident:
        detail = f"{name or 'unnamed'} - light controller ({protocol})"
    elif matched_name:
        detail = f"{name} - looks like a light ({protocol})"
    else:
        detail = f"{name or 'unnamed device'} - {address}"

    return {
        "driver": "mrstar",
        "id": "tv",
        "name": name or "TV Backlight",
        "address": address,
        "protocol": protocol,
        "_detail": detail,
        "_likely": bool(confident or matched_name),
    }


async def find_mrstar(timeout: float = 10.0) -> tuple[list[dict], list[dict], str | None]:
    """Returns (likely lights, every other BLE device, a problem to report).

    The second list matters: these controllers are white-labelled and some
    advertise nothing recognisable, so the user must be able to pick one by
    hand rather than being told nothing was found.
    """
    try:
        from bleak import BleakScanner
    except Exception as exc:
        return [], [], f"Bluetooth support is not installed ({exc})"

    try:
        seen = await BleakScanner.discover(timeout=timeout, return_adv=True)
        entries = [
            _ble_entry(dev.address, adv.local_name or dev.name or "",
                       list(adv.service_uuids or []))
            for dev, adv in seen.values()
        ]
    except Exception as exc:
        # Usually no adapter, or Bluetooth switched off.
        return [], [], f"Bluetooth scan failed: {exc}"

    likely = [e for e in entries if e["_likely"]]
    others = [e for e in entries if not e["_likely"]]
    note = None
    if not entries:
        note = ("No Bluetooth devices at all were seen. Check Bluetooth is on, "
                "and that your PC has an adapter.")
    elif not likely:
        note = (f"Saw {len(entries)} Bluetooth devices but none identify as a "
                "light. Pick yours from the list below.")
    return likely, others, note


# -- Tuya ------------------------------------------------------------------
def parse_tuya_broadcast(data: bytes) -> dict | None:
    """Decode one Tuya UDP discovery packet.

    Gives us the device id and IP. The local key is *not* in here -- it only
    ever comes from your Tuya account, which is why that one field still has
    to be supplied.
    """
    if len(data) < 24 or data[:4] != b"\x00\x00\x55\xaa":
        return None
    try:
        (length,) = struct.unpack(">I", data[12:16])
        payload = data[16:16 + length][:-8]  # strip trailing crc + suffix
    except Exception:
        return None
    if not payload:
        return None

    if not payload.startswith(b"{"):
        payload = _tuya_decrypt(payload)
        if payload is None:
            return None
    try:
        info = json.loads(payload.decode("utf-8", "ignore"))
    except Exception:
        return None
    if not info.get("gwId") or not info.get("ip"):
        return None
    return {
        "driver": "tuya",
        "id": "ceiling",
        "name": "Room Lights",
        "device_id": info["gwId"],
        "address": info["ip"],
        "local_key": "",                  # supplied by the user
        "version": float(info.get("version") or 3.3),
        "_detail": f"Tuya device at {info['ip']} (needs a local key)",
    }


def _tuya_decrypt(payload: bytes) -> bytes | None:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        decryptor = Cipher(algorithms.AES(TUYA_UDP_KEY), modes.ECB()).decryptor()
        clear = decryptor.update(payload) + decryptor.finalize()
        return clear[: -clear[-1]] if clear and clear[-1] <= 16 else clear
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as exc:
        # Broader than Exception: a broken native crypto wheel raises a pyo3
        # panic, which is not an Exception, and must not kill discovery.
        log.debug("could not decrypt Tuya broadcast: %s", exc)
        return None


async def find_tuya(timeout: float = 8.0) -> tuple[list[dict], str | None]:
    """Listen for the beacons Tuya devices broadcast every few seconds."""
    loop = asyncio.get_running_loop()
    seen: dict[str, dict] = {}
    failures: list[str] = []

    class Protocol(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            found = parse_tuya_broadcast(data)
            if found:
                seen.setdefault(found["device_id"], found)

    transports = []
    for port in TUYA_PORTS:
        try:
            transport, _ = await loop.create_datagram_endpoint(
                Protocol, local_addr=("0.0.0.0", port), reuse_port=False,
                allow_broadcast=True,
            )
            transports.append(transport)
        except Exception as exc:
            log.warning("could not listen on udp/%s: %s", port, exc)
            failures.append(f"udp/{port}: {exc}")

    if not transports:
        return [], ("Could not listen for Tuya devices (" + "; ".join(failures)
                    + "). Another app may already be using those ports.")
    try:
        await asyncio.sleep(timeout)
    finally:
        for t in transports:
            t.close()

    note = None
    if not seen:
        note = ("No Tuya beacons heard. These arrive as inbound UDP on ports "
                "6666 and 6667, which the Windows firewall blocks by default "
                "- run setup.ps1 as Administrator to allow them. Also check "
                "the switch is on the same WiFi as this PC.")
    return list(seen.values()), note


# -- everything ------------------------------------------------------------
async def discover_all(timeout: float = 10.0) -> dict:
    """Run every finder at once. One failing never stops the others.

    Reports why a category came back empty, rather than leaving the user
    staring at a zero with nothing to act on.
    """
    wled, mrstar, tuya = await asyncio.gather(
        find_wled(min(timeout, 6.0)),
        find_mrstar(timeout),
        find_tuya(timeout),
        return_exceptions=True,
    )
    notes: dict[str, str] = {}

    def ok(result, label, default):
        if isinstance(result, BaseException):
            log.warning("%s discovery failed: %s", label, result)
            notes[label] = f"{label} scan failed: {result}"
            return default
        return result

    wled = ok(wled, "wled", [])
    if not wled and "wled" not in notes:
        notes["wled"] = ("No WLED found. Check it is powered on and on the "
                         "same WiFi as this PC.")

    likely, others, ble_note = ok(mrstar, "mrstar", ([], [], None))
    if ble_note:
        notes["mrstar"] = ble_note

    tuya_found, tuya_note = ok(tuya, "tuya", ([], None))
    if tuya_note:
        notes["tuya"] = tuya_note

    return {
        "wled": wled,
        "mrstar": likely,
        "tuya": tuya_found,
        "candidates": others,
        "notes": notes,
    }
