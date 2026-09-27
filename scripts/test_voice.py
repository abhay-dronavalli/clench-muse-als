"""Make ONE Spanish and ONE English line with the real ElevenLabs key from .env and save them to the
audio cache (data/audio_cache/ plus the audio_cache table in data/clench.db).

Does nothing real without --send: it only prints what it would make. With --send it always calls
ElevenLabs, even when the lines are already cached, since this script exists to test the key.

    uv run python scripts/test_voice.py --send

Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in .env (ELEVENLABS_MODEL defaults to
eleven_flash_v2_5). Prints each file path and the characters used, or FAILED: <exact error>
(e.g. "ElevenLabs error (HTTP 401) invalid_api_key: ..." = wrong key).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from core.config import load_env  # noqa: E402
from core.contracts import Lang  # noqa: E402
from core.db import DB_PATH, Db  # noqa: E402
from core.voice import AudioCache, ElevenLabsTTS, NullTTS, TTSError, build_tts  # noqa: E402

LINES: list[tuple[str, Lang]] = [
    ("Hola, soy Luis. Esta es mi voz.", "es"),
    ("Hi, I'm Luis. This is my voice.", "en"),
]


async def run(tts: ElevenLabsTTS, cache: AudioCache) -> int:
    assert tts.voice_id is not None
    chars = 0
    try:
        for text, lang in LINES:
            audio = await tts.synthesize(text, lang)
            key = AudioCache.key(text, lang, tts.voice_id, tts.model)
            path = cache.put(key, text, lang, tts.voice_id, audio)
            chars += len(text)
            print(f"OK ({lang}): {len(audio):,} bytes -> {path}")
    except TTSError as e:
        print(f"FAILED: {e}")
        return 1
    finally:
        await tts.aclose()
    print(f"Characters used: {chars} (voice {tts.voice_id}, model {tts.model})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make one Spanish and one English line with ElevenLabs.")
    parser.add_argument("--send", action="store_true", help="really call ElevenLabs (otherwise only print)")
    args = parser.parse_args(argv)
    if not args.send:
        chars = sum(len(text) for text, _ in LINES)
        print(f"Would ask ElevenLabs for {len(LINES)} lines ({chars} characters) and save them to the audio cache:")
        for text, lang in LINES:
            print(f"  ({lang}) {text}")
        print("Nothing sent. Add --send to really call ElevenLabs.")
        return 0
    tts = build_tts(load_env())
    if isinstance(tts, NullTTS):
        print(f"FAILED: ElevenLabs not configured: {tts.reason} (see .env.example)")
        return 1
    assert isinstance(tts, ElevenLabsTTS)
    db = Db(DB_PATH)
    try:
        return asyncio.run(run(tts, AudioCache(db=db)))
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
