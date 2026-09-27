# /// script
# requires-python = ">=3.11"
# dependencies = ["bleak>=1.0,<4"]
# ///
"""Receive Apple Watch heart rate via HeartCast's iPhone BLE broadcast.

Keep HeartCast running on the Watch and open in the foreground on the iPhone
while connecting. Enable Bluetooth on this computer and grant HeartCast its
Bluetooth/Health permissions. Start the Watch session and confirm that BPM is
visible on the iPhone. No OS-level Bluetooth pairing is normally needed.

Run from the repo root (uv manages this script's dependency separately):
    uv run --script scripts/heartcast_ble.py --list
    uv run --script scripts/heartcast_ble.py
    uv run --script scripts/heartcast_ble.py --address AA:BB:CC:DD:EE:FF
    uv run --script scripts/heartcast_ble.py --name HeartCast --json

Default discovery matches 'HeartCast' in the advertised name. If your iPhone
advertises a different name, use --list and then --address or --name. On macOS,
the address is a UUID. Ctrl+C disconnects and exits. Reconnects stay pinned to
the initially selected address; restart discovery if iOS changes that address.

Only receives measurements and prints them; no Clench imports, file writes,
cloud calls, or patient database access. JSON timestamps are computer receipt
times in UTC, not Watch measurement times. Optional RR/energy/contact fields
are reported only when supplied by the broadcaster.

References:
https://www.heartcast.app/faq-help-support-issues/
https://bleak.readthedocs.io/en/stable/api/client.html
https://www.bluetooth.com/specifications/specs/heart-rate-service-1-0/
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import sys

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_measurement(data: bytes | bytearray) -> dict:
    """Decode the standard Bluetooth Heart Rate Measurement, little endian."""
    if not data:
        raise ValueError("Missing measurement flags")
    flags, offset = data[0], 1

    def take(width: int) -> int:
        nonlocal offset
        if offset + width > len(data):
            raise ValueError("Truncated heart rate measurement")
        value = int.from_bytes(data[offset:offset + width], "little")
        offset += width
        return value

    result = {"bpm": take(2 if flags & 0x01 else 1)}
    if flags & 0x04:
        result["sensor_contact"] = bool(flags & 0x02)
    if flags & 0x08:
        result["energy_expended_kj"] = take(2)
    if flags & 0x10:
        if offset == len(data) or (len(data) - offset) % 2:
            raise ValueError("Invalid RR interval payload")
        result["rr_intervals_ms"] = [
            take(2) * 1000 / 1024 for _ in range((len(data) - offset) // 2)
        ]
    if offset != len(data):
        raise ValueError("Unexpected trailing measurement bytes")
    return result


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("Must be a finite number greater than zero")
    return seconds


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List nearby BLE devices and exit")
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--address", help="Exact BLE address (or macOS device UUID)")
    selector.add_argument("--name", default=None, help="Advertised name substring (default: HeartCast)")
    parser.add_argument("--json", action="store_true", help="One JSON measurement per stdout line")
    parser.add_argument("--scan-timeout", type=positive_seconds, default=10.0)
    parser.add_argument("--retry-delay", type=positive_seconds, default=3.0)
    parser.add_argument("--stale-timeout", type=positive_seconds, default=30.0,
                        help="Reconnect after this many seconds without valid data (default: 30)")
    args = parser.parse_args()
    if args.name is not None and not args.name.strip():
        parser.error("--name cannot be blank")
    if args.address is not None and not args.address.strip():
        parser.error("--address cannot be blank")
    return args


def status(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


async def stream(args: argparse.Namespace) -> None:
    try:
        from bleak import BleakClient, BleakScanner
        from bleak.exc import BleakError
    except ImportError as exc:
        raise RuntimeError(
            "Missing BLE dependency. Run: uv run --script scripts/heartcast_ble.py"
        ) from exc

    selected_address = args.address
    loop = asyncio.get_running_loop()
    while True:
        try:
            status("Scanning BLE devices...")
            found = await BleakScanner.discover(timeout=args.scan_timeout, return_adv=True)
            if args.list:
                for device, advertisement in found.values():
                    name = advertisement.local_name or device.name or "(unnamed)"
                    is_hr = HEART_RATE_SERVICE in advertisement.service_uuids
                    print(f"{device.address}  {name}  RSSI={advertisement.rssi}"
                          f"{'  [Heart Rate]' if is_hr else ''}", flush=True)
                if not found:
                    status("No devices found. Check Bluetooth and keep HeartCast visible on iPhone.")
                return

            matches = []
            for device, advertisement in found.values():
                if selected_address:
                    match = device.address.casefold() == selected_address.casefold()
                else:
                    names = (advertisement.local_name or "", device.name or "")
                    match = any((args.name or "HeartCast").casefold() in name.casefold()
                                for name in names)
                if match:
                    matches.append(device)
            if len(matches) > 1:
                addresses = ", ".join(device.address for device in matches)
                raise RuntimeError(f"Multiple devices match: {addresses}. Choose one with --address.")
            if not matches:
                status("Device not found. Keep HeartCast visible on iPhone; use --list to check its name.")
            else:
                device = matches[0]
                disconnected = asyncio.Event()
                last_received = loop.time()

                def on_disconnect(_client) -> None:
                    loop.call_soon_threadsafe(disconnected.set)

                def on_measurement(_characteristic, data: bytearray) -> None:
                    nonlocal last_received
                    try:
                        measurement = parse_measurement(data)
                    except ValueError as exc:
                        status(f"Ignoring malformed measurement: {exc}")
                        return
                    last_received = loop.time()
                    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    if args.json:
                        print(json.dumps({"timestamp": timestamp, "device": device.address,
                                          **measurement}), flush=True)
                    else:
                        print(f"{timestamp}  {measurement['bpm']} BPM", flush=True)

                status(f"Connecting to {device.name or 'BLE device'} ({device.address})...")
                async with BleakClient(device, disconnected_callback=on_disconnect,
                                       timeout=20.0) as client:
                    characteristic = client.services.get_characteristic(HEART_RATE_MEASUREMENT)
                    if characteristic is None:
                        raise RuntimeError("Selected device has no Heart Rate Measurement characteristic. "
                                           "Run --list and select the HeartCast iPhone broadcast.")
                    await client.start_notify(characteristic, on_measurement)
                    selected_address = device.address
                    last_received = loop.time()
                    status("Streaming heart rate. Press Ctrl+C to stop.")
                    while not disconnected.is_set():
                        try:
                            await asyncio.wait_for(disconnected.wait(), timeout=1.0)
                        except TimeoutError:
                            if loop.time() - last_received >= args.stale_timeout:
                                status("No fresh measurements; reconnecting. Check the Watch session.")
                                break
                status("Disconnected.")
        except (BleakError, OSError, TimeoutError) as exc:
            if args.list:
                raise RuntimeError(f"BLE scan failed: {exc}") from exc
            status(f"BLE error: {exc}")
        status(f"Retrying in {args.retry_delay:g} seconds...")
        await asyncio.sleep(args.retry_delay)


def main() -> int:
    args = arguments()
    try:
        asyncio.run(stream(args))
    except KeyboardInterrupt:
        status("Stopped.")
    except RuntimeError as exc:
        status(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
