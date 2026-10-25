"""Optional authenticated SpeechKit synthesis. No provider calls unless explicitly enabled."""
import asyncio
import math
import os
import time
import logging
from collections import deque
from aiohttp import ClientSession, ClientTimeout, ClientError, web
from velia_desktop_routes import AuthenticationUnavailable

VOICES = [{"voiceURI": "speechkit:" + key, "name": name, "lang": "ru-RU", "localService": False}
          for key, name in [("jane", "Джейн"), ("omazh", "Омаж"), ("marina", "Марина")]]
ENDPOINT = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
LOG = logging.getLogger(__name__)
LANGUAGES = ["ru-RU", "en-US", "tr-TR", "de-DE", "fr-FR", "es-ES"]
EDGE_VOICES = [
    {"voiceURI": "edge:ru-RU-SvetlanaNeural", "name": "Светлана", "lang": "ru-RU",
     "gender": "female", "recommended": True, "localService": False},
    *[{"voiceURI": "edge:en-US-" + key + "MultilingualNeural", "name": name,
       "lang": "en-US", "languages": LANGUAGES, "gender": "female", "localService": False}
      for key, name in [("Emma", "Эмма"), ("Ava", "Ава")]],
]

def enabled():
    return (os.getenv("VELIA_WEB_TTS_PROVIDER") == "edge"
        or os.getenv("VELIA_WEB_TTS_PROVIDER") == "speechkit" and bool(os.getenv("VELIA_SPEECHKIT_API_KEY")))

def voice_catalog():
    if not enabled():
        return []
    return EDGE_VOICES if os.getenv("VELIA_WEB_TTS_PROVIDER") == "edge" else VOICES

def validate(data):
    if not isinstance(data, dict) or set(data) != {"text", "voice", "rate"}:
        raise ValueError()
    text, voice, rate = data["text"], data["voice"], data["rate"]
    if (not isinstance(text, str) or not text.strip() or len(text) > 500
            or voice not in {v["voiceURI"] for v in voice_catalog()}
            or type(rate) not in {int, float} or not math.isfinite(rate) or not .7 <= rate <= 1.4):
        raise ValueError()
    if voice.startswith("edge:"):
        return {"text": text, "edge_voice": voice.split(":", 1)[1], "edge_rate": f"{round((rate-1)*100):+d}%"}
    return {"text": text, "voice": voice.split(":", 1)[1], "speed": str(rate), "lang": "ru-RU", "format": "mp3"}

async def synthesize_edge(fields):
    # Fixed provider/allowlisted speakers; no client URL, credentials or SSML.
    import edge_tts
    started, first, chunks, size = time.monotonic(), None, [], 0
    try:
        async with asyncio.timeout(20):
            source = edge_tts.Communicate(fields["text"], fields["edge_voice"],
                rate=fields["edge_rate"], connect_timeout=5, receive_timeout=10)
            async for part in source.stream():
                if part["type"] != "audio":
                    continue
                first = first or time.monotonic()
                size += len(part["data"])
                if size > 2 * 1024 * 1024:
                    raise RuntimeError("tts_unavailable")
                chunks.append(part["data"])
        if not size:
            raise RuntimeError("tts_unavailable")
    except asyncio.CancelledError:
        raise
    except Exception:
        raise RuntimeError("tts_unavailable") from None
    LOG.info("VELIA_WEB_TTS_TIMING provider=edge voice=%s first_audio_ms=%d total_ms=%d",
        fields["edge_voice"], round((first-started)*1000), round((time.monotonic()-started)*1000))
    return b"".join(chunks)

async def synthesize(fields):
    if "edge_voice" in fields:
        return await synthesize_edge(fields)
    # No redirects: the server key must only reach the documented fixed endpoint.
    async with ClientSession(timeout=ClientTimeout(total=15)) as client:
        async with client.post(ENDPOINT, data=fields, allow_redirects=False,
                               headers={"Authorization": "Api-Key " + os.environ["VELIA_SPEECHKIT_API_KEY"]}) as response:
            if response.status != 200:
                raise RuntimeError("tts_unavailable")
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise RuntimeError("tts_unavailable")
                chunks.append(chunk)
            if not size:
                raise RuntimeError("tts_unavailable")
            return b"".join(chunks)

def setup_voice_routes(app, *, session_for, same_origin, json_response):
    requests = {}
    slots = asyncio.Semaphore(4)
    async def catalog(request):
        try:
            if not await session_for(request):
                return json_response({"error": "unauthorized"}, 401)
            return json_response({"voices": voice_catalog()})
        except AuthenticationUnavailable:
            return json_response({"error": "authentication_unavailable"}, 503)
    async def speech(request):
        if not same_origin(request):
            return json_response({"error": "invalid_origin"}, 403)
        try:
            session = await session_for(request)
            if not session:
                return json_response({"error": "unauthorized"}, 401)
            if not enabled():
                return json_response({"error": "tts_not_configured"}, 503)
            if request.content_length and request.content_length > 8192:
                return json_response({"error": "request_too_large"}, 413)
            fields = validate(await request.json())
            now = time.monotonic()
            for key in list(requests):
                if not requests[key] or requests[key][-1] <= now - 60:
                    del requests[key]
            history = requests.setdefault(session.user_id, deque())
            while history and history[0] <= now - 60:
                history.popleft()
            if len(history) >= 40 or slots.locked():
                return json_response({"error": "tts_rate_limit"}, 429)
            history.append(now)
            async with slots:
                audio = await synthesize(fields)
            return web.Response(body=audio, content_type="audio/mpeg", headers={"Cache-Control": "no-store"})
        except AuthenticationUnavailable:
            return json_response({"error": "authentication_unavailable"}, 503)
        except (ValueError, TypeError):
            return json_response({"error": "invalid_speech_request"}, 400)
        except (ClientError, asyncio.TimeoutError, RuntimeError):
            return json_response({"error": "tts_unavailable"}, 503)
    app.router.add_get("/web-api/v1/voice/voices", catalog)
    app.router.add_post("/web-api/v1/voice/speech", speech)
