"""Works out which nearby Bluetooth device is the light, by asking it.

Cheap LED controllers often advertise no name and no services, so scanning
alone cannot identify them. Connecting does: the GATT table shows which
characteristic accepts colour commands. Flashing the light then confirms it
beyond doubt -- the user simply looks at the wall.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# Characteristics these boards accept colour writes on, and the dialect each
# implies. fff3 and ffe1 are serial-passthrough modules that still take the
# Triones 0x56 packet.
WRITE_CHARS = {
    "0000ffd9-0000-1000-8000-00805f9b34fb": "triones",
    "0000ff01-0000-1000-8000-00805f9b34fb": "lednet",
    "0000fff3-0000-1000-8000-00805f9b34fb": "triones",
    "0000ffe1-0000-1000-8000-00805f9b34fb": "triones",
    "0000ffe9-0000-1000-8000-00805f9b34fb": "triones",
}

# Addresses in this range are rotating privacy addresses -- phones, watches,
# laptops. LED controllers use fixed ones, so these are tried last.
def is_private_address(address: str) -> bool:
    try:
        first = int(address.split(":")[0], 16)
    except Exception:
        return False
    return 0x40 <= first <= 0x7F


def rank(entry: dict) -> tuple:
    """Most-likely-to-be-a-light first, so a sweep finds it early."""
    name = (entry.get("name") or "").lower()
    named_like_a_light = any(
        k in name for k in ("led", "rgb", "star", "gatt", "demo", "ble", "light")
    )
    return (
        not entry.get("_likely", False),
        not named_like_a_light,
        is_private_address(entry.get("address", "")),
        -(entry.get("rssi") or -999),
    )


async def probe(address: str, timeout: float = 12.0) -> dict:
    """Connect and report whether this device can be driven as a light."""
    from bleak import BleakClient

    result = {"address": address, "ok": False, "char": None,
              "protocol": None, "error": None, "services": []}
    try:
        async with BleakClient(address, timeout=timeout) as client:
            uuids = []
            for service in client.services:
                for ch in service.characteristics:
                    uuids.append(ch.uuid.lower())
                    writable = ("write" in ch.properties
                                or "write-without-response" in ch.properties)
                    if writable and ch.uuid.lower() in WRITE_CHARS and not result["char"]:
                        result["char"] = ch.uuid.lower()
                        result["protocol"] = WRITE_CHARS[ch.uuid.lower()]
            result["services"] = uuids
            result["ok"] = bool(result["char"])
            if not result["ok"]:
                result["error"] = "connected, but no known colour characteristic"
    except Exception as exc:
        result["error"] = str(exc)
    return result


async def flash(address: str, protocol: str = "triones",
                char: str | None = None, rounds: int = 3) -> dict:
    """Drive the light through red, green and blue so it can be recognised.

    This is the part that removes the guesswork: the user watches the wall
    instead of trying protocols at random.
    """
    from .drivers.mrstar import MrStarLight

    light = MrStarLight("probe", "probe", address, protocol=protocol, char=char)
    try:
        await light.connect()
        for _ in range(rounds):
            for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                await light.set(on=True, color=colour, brightness=255,
                                throttle=False)
                await asyncio.sleep(0.45)
        await light.set(on=False, throttle=False)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            await light.disconnect()
        except Exception:
            pass


async def find_the_light(candidates: list[dict], timeout: float = 12.0,
                         limit: int = 8) -> dict:
    """Probe candidates in likelihood order, stopping at the first match."""
    ordered = sorted(candidates, key=rank)[:limit]
    tried = []
    for entry in ordered:
        result = await probe(entry["address"], timeout=timeout)
        tried.append({"address": entry["address"],
                      "name": entry.get("name", ""),
                      "ok": result["ok"], "error": result["error"]})
        if result["ok"]:
            return {"found": {**entry, "protocol": result["protocol"],
                              "char": result["char"],
                              "driver": "mrstar", "id": "tv",
                              "name": entry.get("name") or "TV Backlight",
                              "_detail": f"confirmed light "
                                         f"({result['protocol']} on "
                                         f"{result['char'][4:8]})",
                              "_likely": True},
                    "tried": tried}
    return {"found": None, "tried": tried}


async def _main() -> None:
    from bleak import BleakScanner

    print("Scanning...\n")
    seen = await BleakScanner.discover(timeout=10.0, return_adv=True)
    candidates = [
        {"address": d.address, "name": adv.local_name or d.name or "",
         "rssi": adv.rssi}
        for d, adv in seen.values()
    ]
    print(f"{len(candidates)} devices. Connecting to the likeliest "
          f"(this takes a moment each)...\n")

    result = await find_the_light(candidates)
    for t in result["tried"]:
        mark = "OK " if t["ok"] else "   "
        print(f"{mark}{t['address']}  {t['name'] or '(no name)'}"
              + ("" if t["ok"] else f"  -- {t['error']}"))

    found = result["found"]
    if found:
        print(f"\nFound it: {found['address']} ({found['protocol']})")
        print("Flashing it red, green, blue -- watch your light...")
        await flash(found["address"], found["protocol"], found["char"])
        print("\nIf that was your TV backlight, paste this address into the")
        print(f"setup page: {found['address']}")
    else:
        print("\nNone of them accepted colour commands.")
        print("The light may be connected to its phone app -- these boards")
        print("allow only one connection at a time. Close it and retry.")


if __name__ == "__main__":
    asyncio.run(_main())
