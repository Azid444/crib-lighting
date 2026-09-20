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

# Names these rebadged BLE controllers advertise under.
BLE_NAMES = ("mrstar", "mr-star", "lednetwf", "triones", "ledble", "ledblue",
             "melk", "happy lighting", "ihoment", "qhm-")


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


async def find_mrstar(timeout: float = 10.0) -> list[dict]:
    try:
        from bleak import BleakScanner
    except Exception as exc:
        log.warning("Bluetooth unavailable: %s", exc)
        return []

    try:
        devices = await BleakScanner.discover(timeout=timeout)
    except Exception as exc:
        log.warning("BLE scan failed: %s", exc)
        return []

    out = []
    for d in devices:
        name = d.name or ""
        if not any(p in name.lower() for p in BLE_NAMES):
            continue
        out.append({
            "driver": "mrstar",
            "id": "tv",
            "name": name or "TV Backlight",
            "address": d.address,
            "protocol": guess_protocol(name),
            "_detail": f"{name} ({guess_protocol(name)} protocol)",
        })
    return out


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


async def find_tuya(timeout: float = 8.0) -> list[dict]:
    """Listen for the beacons Tuya devices broadcast every few seconds."""
    loop = asyncio.get_running_loop()
    seen: dict[str, dict] = {}

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
            log.debug("could not listen on udp/%s: %s", port, exc)

    if not transports:
        return []
    try:
        await asyncio.sleep(timeout)
    finally:
        for t in transports:
            t.close()
    return list(seen.values())


# -- everything ------------------------------------------------------------
async def discover_all(timeout: float = 10.0) -> dict:
    """Run every finder at once. One failing never stops the others."""
    wled, mrstar, tuya = await asyncio.gather(
        find_wled(min(timeout, 6.0)),
        find_mrstar(timeout),
        find_tuya(timeout),
        return_exceptions=True,
    )

    def ok(result, label):
        if isinstance(result, BaseException):
            log.warning("%s discovery failed: %s", label, result)
            return []
        return result

    return {
        "wled": ok(wled, "WLED"),
        "mrstar": ok(mrstar, "MR-Star"),
        "tuya": ok(tuya, "Tuya"),
    }
