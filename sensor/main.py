"""Muse jaw commands to the web app. Run: uv run --extra sensor python -m sensor.main --profile taher."""
import argparse
import asyncio
import contextlib
import json
import logging
import math
import time

from core.contracts import Settings, parse_message
from sensor.profiles import load_profile

log = logging.getLogger('clench.sensor')


def status(profile, *, sample=None, connected=False, blocked=None, threshold=None):
    return dict(type='SIGNAL', t=time.time(), ch=sample.channels if sample else [],
                connected=connected, profile=profile, emg=sample.level if sample else None,
                threshold=threshold, blocked=blocked or (sample.blocked if sample else None))


async def run_connection(ws, source_factory, profile, profile_name, *, retry_delay=5., blink=False):
    from sensor.detect.input import ClenchInput
    # Clenches use this person's calibration; blinks use MNE, which needs none (as in the bench).
    detector = ClenchInput(profile)
    log.info('clench: calibrated threshold %.1f uV (rest %.1f uV)', profile['emg_threshold'], profile['emg_rest'])
    log.info('double blink (back): %s', 'MNE on AF7/AF8' if blink else 'off')
    eyes = None
    enabled = False
    settings_received = False

    async def receive():
        nonlocal enabled, settings_received
        async for raw in ws:
            msg = parse_message(json.loads(raw))
            if isinstance(msg, Settings):
                new_enabled = bool(msg.muse_enabled)
                new_long = msg.long_clench_ms or 2500
                if new_enabled != enabled or new_long != detector.long_ms:
                    detector.reset()
                enabled = new_enabled
                detector.long_ms = new_long
                settings_received = True

    reader = asyncio.create_task(receive())
    connected = False
    source = None
    next_status = 0.
    attempts = 0
    try:
        while True:
            if reader.done():
                await reader  # propagate a settings/socket error
                raise ConnectionError('Core disconnected')
            if not connected:
                detector.reset()
                await ws.send(json.dumps(status(profile_name, blocked='Connecting to Muse', threshold=profile['emg_threshold'])))
                try:
                    # BrainFlow's native Muse transport is not safe to reuse after
                    # BOARD_NOT_READY_ERROR. Rebuild the wrapper for every attempt
                    # so a failed Bluetooth session cannot poison the next retry.
                    source = source_factory()
                    await asyncio.to_thread(source.open)
                    connected = True
                    attempts = 0
                    log.info('Headband connected; enable Muse clenches in the web UI when ready')
                    if blink:
                        from sensor.detect.eyes import MNEEyes
                        eyes = MNEEyes(256, time.time())
                        log.info('MNE blinks warming up: needs 20 s of continuous AF7/AF8')
                except Exception as exc:
                    if source is not None:
                        with contextlib.suppress(Exception):
                            await asyncio.to_thread(source.close)
                        source = None
                    attempts += 1
                    # Back off: hammering the adapter every 5 s after a dropped link keeps it busy
                    # and BrainFlow just answers BOARD_NOT_READY_ERROR again.
                    wait = min(retry_delay*2**min(attempts-1, 3), 40.)
                    log.warning('Headband connection failed (attempt %d): %s; retrying in %.0f s',
                                attempts, exc, wait)
                    await asyncio.sleep(wait)
                    continue
            try:
                assert source is not None
                sample = await asyncio.to_thread(source.read)
            except Exception as exc:
                log.warning('Headband lost: %s', exc)
                detector.reset()
                connected = False
                if eyes is not None:
                    eyes.close()
                    eyes = None
                if source is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(source.close)
                    source = None
                await ws.send(json.dumps(status(profile_name, blocked='Headband disconnected — reconnecting', threshold=profile['emg_threshold'])))
                await asyncio.sleep(retry_delay)
                continue
            now = time.time()
            commands = detector.update(sample.level, now, enabled=enabled and settings_received,
                                       blocked=sample.blocked)
            if eyes is not None:
                if eyes.due(now):
                    window = await asyncio.to_thread(source.eyes, eyes.window_samples)
                    eyes.submit(*(window or (None, float('nan'))), now)
                blinks, notes = eyes.poll(now)
                for note in notes:
                    log.info('%s', note)
                # A clench wins the tick: the jaw pulls the brow, so never send both at once.
                if not commands:
                    commands = blinks
            reason = sample.blocked or ('Paused in web UI' if not enabled else None)
            if not reason and not detector.armed:
                reason = 'Relax jaw to arm'
            if now >= next_status or commands:
                await ws.send(json.dumps(status(profile_name, sample=sample, connected=True,
                    blocked=reason, threshold=profile['emg_threshold']), allow_nan=False))
                next_status = now+.25
            for command in commands:
                await ws.send(json.dumps(command))
                log.info('%s sent', command['type'])
            await asyncio.sleep(.05)
    finally:
        if eyes is not None:
            eyes.close()
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader
        if source is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(source.close)


async def run(args):
    from websockets.asyncio.client import connect
    from sensor.sources.muse import DemoSource, MuseSource
    profile = DemoSource.profile if args.source == 'demo' else load_profile(args.profile)
    profile_name = 'DEMO (simulated)' if args.source == 'demo' else args.profile
    while True:
        try:
            async with connect(args.url, open_timeout=5, max_queue=8) as ws:
                source_factory = (DemoSource if args.source == 'demo'
                                  else lambda: MuseSource(args.name, args.motion_limit))
                blink = args.blink != 'off'
                await run_connection(ws, source_factory, profile, profile_name, blink=blink)
        except Exception as exc:
            log.warning('Input connection stopped: %s; retrying in 5 s', exc)
            await asyncio.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='taher')
    parser.add_argument('--source', choices=('muse', 'demo'), default='muse')
    parser.add_argument('--name', help='Muse Bluetooth name, e.g. Muse-1234')
    parser.add_argument('--url', default='ws://127.0.0.1:8000/ws/sensor')
    parser.add_argument('--motion-limit', type=float, default=30., help='Gyroscope limit in degrees/s')
    parser.add_argument('--blink', choices=('auto', 'on', 'off'), default='auto',
                        help='DOUBLE_BLINK (back) from MNE on AF7/AF8: auto and on run it, off does not')
    args = parser.parse_args()
    if not math.isfinite(args.motion_limit) or args.motion_limit <= 0:
        parser.error('--motion-limit must be positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
