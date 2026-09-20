"""Works out which nearby Bluetooth device is the light, by asking it.

Cheap LED controllers often advertise no name and no services, so scanning
alone cannot identify them. Connecting does: the GATT table shows which
characteristic accepts colour commands. Flashing the light then confirms it
beyond doubt -- the user simply looks at the wall.
"""
from __future__ import annotations

import asyncio
import logging
import sys

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


# Connection errors that mean "something else already has this device".
# These boards accept exactly one connection, so the phone app wins.
BUSY_ERRORS = ("unreachable", "was not found", "not connected",
               "device is busy", "access denied")


def explain_error(error: str | None) -> str | None:
    """Turn a BLE error into something worth acting on."""
    if not error:
        return None
    if any(marker in error.lower() for marker in BUSY_ERRORS):
        return ("the device did not accept a connection - it is usually still "
                "connected to its app on your phone; force-close that app and "
                "retry")
    return None


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


async def describe(address: str, timeout: float = 15.0,
                   attempts: int = 2) -> dict:
    """Full GATT table for one device.

    "Unreachable" is common on first contact with these boards, so a failed
    connection is retried before giving up.
    """
    from bleak import BleakClient

    last = None
    for attempt in range(attempts):
        try:
            async with BleakClient(address, timeout=timeout) as client:
                out = []
                for service in client.services:
                    for ch in service.characteristics:
                        out.append({
                            "uuid": ch.uuid.lower(),
                            "service": service.uuid.lower(),
                            "properties": list(ch.properties),
                            "writable": bool(
                                {"write", "write-without-response"}
                                & set(ch.properties)),
                        })
                return {"address": address, "ok": True, "error": None,
                        "characteristics": out}
        except Exception as exc:
            last = str(exc)
            if attempt + 1 < attempts:
                await asyncio.sleep(1.5)
    return {"address": address, "ok": False, "error": last,
            "characteristics": []}


async def flash_with(address: str, char: str, protocol: str,
                     seconds: float = 4.0) -> dict:
    """Flash using one specific characteristic, whether or not it is known."""
    from .drivers.mrstar import MrStarLight

    light = MrStarLight("probe", "probe", address, protocol=protocol, char=char)
    try:
        await light.connect()
        deadline = asyncio.get_running_loop().time() + seconds
        while asyncio.get_running_loop().time() < deadline:
            for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                await light.set(on=True, color=colour, brightness=255,
                                throttle=False)
                await asyncio.sleep(0.4)
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        try:
            await light.disconnect()
        except Exception:
            pass


def writable_candidates(chars: list[dict]) -> list[dict]:
    """Writable characteristics, known ones first.

    Vendors put colour control on all sorts of UUIDs, so anything writable is
    worth a try once the known ones are exhausted.
    """
    known = [c for c in chars if c["writable"] and c["uuid"] in WRITE_CHARS]
    rest = [c for c in chars if c["writable"] and c["uuid"] not in WRITE_CHARS]
    return known + rest


async def probe(address: str, timeout: float = 12.0) -> dict:
    """Connect and report whether this device can be driven as a light."""
    info = await describe(address, timeout=timeout)
    result = {"address": address, "ok": False, "char": None, "protocol": None,
              "error": info["error"],
              "services": [c["uuid"] for c in info["characteristics"]]}
    if not info["ok"]:
        return result

    for c in info["characteristics"]:
        if c["writable"] and c["uuid"] in WRITE_CHARS:
            result.update(char=c["uuid"], protocol=WRITE_CHARS[c["uuid"]],
                          ok=True, error=None)
            return result

    writable = sum(1 for c in info["characteristics"] if c["writable"])
    result["error"] = (f"connected, but no known colour characteristic "
                       f"({writable} writable, try --hunt {address})")
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


async def _dump(addresses: list[str]) -> None:
    for address in addresses:
        print(f"\n=== {address} ===")
        info = await describe(address)
        if not info["ok"]:
            print(f"  could not connect: {info['error']}")
            continue
        for c in info["characteristics"]:
            mark = "W" if c["writable"] else " "
            known = "  <- known colour characteristic" if c["uuid"] in WRITE_CHARS else ""
            print(f"  {mark} {c['uuid']}  {','.join(c['properties'])}{known}")
        writable = writable_candidates(info["characteristics"])
        print(f"  ({len(writable)} writable)")


def _ask(question: str) -> bool:
    try:
        return input(question).strip().lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


async def _hunt(address: str) -> None:
    """Try every writable characteristic until the user sees the light react."""
    print(f"Connecting to {address}...")
    info = await describe(address)
    if not info["ok"]:
        print(f"Could not connect: {info['error']}")
        hint = explain_error(info["error"])
        print(f"\n{hint.capitalize()}." if hint else
              "\nCheck the device is powered on and in range.")
        print("\nIn the MR-Star app this shows as 'Connected' under Bound")
        print("Device. Force-close the app (swipe it away, do not just")
        print("background it), then run this again.")
        return

    options = [(c["uuid"], p) for c in writable_candidates(info["characteristics"])
               for p in ("triones", "lednet")]
    if not options:
        print("This device has nothing writable, so it is not the light.")
        return

    print(f"\n{len(options)} things to try. Watch your TV backlight.\n")
    for i, (char, protocol) in enumerate(options, 1):
        print(f"[{i}/{len(options)}] {char[4:8]} as {protocol} ... ", end="", flush=True)
        result = await flash_with(address, char, protocol)
        if not result["ok"]:
            print(f"failed ({result['error']})")
            continue
        print("sent")
        if _ask("        Did your light flash? [y/N] "):
            print(f"""
Found it. Add this to the devices list in config.yaml:

  - driver: mrstar
    id: tv
    name: TV Backlight
    address: {address}
    protocol: {protocol}
    char: "{char}"
""")
            return
    print("\nNothing on this device drove the light. Try another address.")


async def _sweep() -> None:
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
        hint = explain_error(t["error"])
        if hint:
            print(f"   {'':18}  ^ {hint}")

    found = result["found"]
    if found:
        print(f"\nFound it: {found['address']} ({found['protocol']})")
        print("Flashing it red, green, blue -- watch your light...")
        await flash(found["address"], found["protocol"], found["char"])
        print(f"\nIf that was your backlight, use address {found['address']}")
        return

    reachable = [t["address"] for t in result["tried"]
                 if t["error"] and "no known colour" in t["error"]]
    print("\nNone of them used a characteristic I recognise.")
    if reachable:
        print("\nThese connected fine, so one of them may still be it, just")
        print("using an unusual characteristic. Try each in turn:\n")
        for address in reachable:
            print(f"    python -m crib.probe --hunt {address}")
        print("\nThat flashes every writable characteristic and asks what you saw.")
    else:
        print("\nNothing connected. Close the light's app on your phone --")
        print("these boards allow only one connection at a time.")


def _cli() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--dump":
        asyncio.run(_dump(args[1:]))
    elif args and args[0] == "--hunt":
        if len(args) < 2:
            sys.exit("usage: python -m crib.probe --hunt <address>")
        asyncio.run(_hunt(args[1]))
    elif args:
        sys.exit("usage: python -m crib.probe [--dump ADDR...] [--hunt ADDR]")
    else:
        asyncio.run(_sweep())


if __name__ == "__main__":
    _cli()
