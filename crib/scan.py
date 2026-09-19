"""Find your MR-Star's MAC address: python -m crib.scan"""
import asyncio

from bleak import BleakScanner


async def main() -> None:
    print("Scanning for 10s...\n")
    for d in await BleakScanner.discover(timeout=10.0):
        print(f"{d.address}   {d.name or '(no name)'}")
    print("\nLook for a name like MRSTAR / LEDnetWF / Triones / LEDBLE.")


if __name__ == "__main__":
    asyncio.run(main())
