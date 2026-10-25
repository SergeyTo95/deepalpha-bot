"""One-time provider acceptance with synthetic text; never logs submitted text."""
import asyncio
import json
import time
from desktop.voice_routes import EDGE_VOICES, synthesize, validate, voice_catalog


async def main():
    try:
        import edge_tts
        if voice_catalog() != EDGE_VOICES:
            raise RuntimeError("edge_provider_not_configured")
        inventory = await asyncio.wait_for(edge_tts.list_voices(), 15)
        women = {v["ShortName"] for v in inventory if v.get("Gender") == "Female"}
        results = []
        for voice in EDGE_VOICES:
            if voice["voiceURI"].split(":", 1)[1] not in women:
                raise RuntimeError("speaker_unavailable")
            started = time.monotonic()
            audio = await synthesize(validate({"text": "Привет! Я Велия.", "voice": voice["voiceURI"], "rate": 1}))
            if len(audio) < 1000:
                raise RuntimeError("audio_unavailable")
            results.append({"voice": voice["voiceURI"], "bytes": len(audio),
                "elapsed_ms": round((time.monotonic()-started)*1000)})
        print("VELIA_WEB_VOICE_OK " + json.dumps(results), flush=True)
        return 0
    except Exception:
        print("VELIA_WEB_VOICE_FAILED", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
