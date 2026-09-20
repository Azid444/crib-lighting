"""List every Bluetooth device nearby, and say which look like lights.

    .venv\\Scripts\\python.exe -m crib.scan

Use this when the setup wizard finds no TV backlight: it shows whether the
adapter works at all, and prints the address to paste into the wizard.
"""
import asyncio
import sys

from .discover import BLE_SERVICES, _ble_entry


async def main() -> None:
    try:
        from bleak import BleakScanner
    except Exception as exc:
        sys.exit(f"Bluetooth support is not installed: {exc}")

    print("Scanning for 12 seconds...\n")
    try:
        seen = await BleakScanner.discover(timeout=12.0, return_adv=True)
    except Exception as exc:
        print(f"The scan failed: {exc}\n")
        print("Usually this means Bluetooth is switched off, or this PC has no")
        print("Bluetooth adapter. A USB BLE dongle is about a tenner.")
        return

    if not seen:
        print("No Bluetooth devices at all were found.\n")
        print("Check that Bluetooth is on, that the light is powered up, and")
        print("that it is NOT connected to its app on your phone -- these")
        print("controllers only accept one connection at a time.")
        return

    rows = []
    for device, adv in seen.values():
        name = adv.local_name or device.name or ""
        entry = _ble_entry(device.address, name, list(adv.service_uuids or []))
        rows.append((entry["_likely"], device.address, name,
                     entry["protocol"], adv.rssi,
                     list(adv.service_uuids or [])))
    rows.sort(key=lambda r: (not r[0], -(r[4] or -999)))

    print(f"{'':2} {'address':<20} {'signal':>7}  name")
    print("-" * 66)
    for likely, address, name, protocol, rssi, uuids in rows:
        mark = "->" if likely else "  "
        print(f"{mark} {address:<20} {str(rssi) + ' dBm':>7}  {name or '(no name)'}")
        if likely:
            known = [u for u in uuids if u.lower() in BLE_SERVICES]
            why = f"service {known[0][4:8]}" if known else "name"
            print(f"{'':2} {'':<20} {'':>7}  ^ looks like a light "
                  f"({protocol}, matched on {why})")

    likely_rows = [r for r in rows if r[0]]
    print()
    if likely_rows:
        print(f"{len(likely_rows)} likely light(s), marked with ->.")
        print("Paste the address into the setup page and pick that protocol.")
    else:
        print("Nothing identified itself as a light.")
        print("The MR-Star often does not, so if you recognise one of the")
        print("devices above, paste its address into the setup page under")
        print('"Add a light by hand" and try triones first, then lednet.')


if __name__ == "__main__":
    asyncio.run(main())
