"""Optional authenticated SpeechKit synthesis. No provider calls unless explicitly enabled."""
import asyncio
import math
import os
import time
import logging
from collections import deque
from contextlib import aclosing
from aiohttp import ClientSession, ClientTimeout, ClientError, ClientConnectionError, ClientResponseError, web
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
    if (not isinstance(text, str) or not any(c.isalnum() for c in text) or len(text) > 500
            or voice not in {v["voiceURI"] for v in voice_catalog()}
            or type(rate) not in {int, float} or not math.isfinite(rate) or not .7 <= rate <= 1.4):
        raise ValueError()
    if voice.startswith("edge:"):
        return {"text": text, "edge_voice": voice.split(":", 1)[1], "edge_rate": f"{round((rate-1)*100):+d}%"}
    return {"text": text, "voice": voice.split(":", 1)[1], "speed": str(rate), "lang": "ru-RU", "format": "mp3"}

async def stream_edge(fields):
    # Fixed provider/allowlisted speakers; no client URL, credentials or SSML.
    import edge_tts
    started, first, size = time.monotonic(), None, 0
    attempts = 0
    try:
        for attempt in range(2):
            attempts += 1
            try:
                speaker = edge_tts.Communicate(fields["text"], fields["edge_voice"],
                    rate=fields["edge_rate"], connect_timeout=5, receive_timeout=10)
                async with aclosing(speaker.stream()) as source:
                    while True:
                        # End the timeout scope before yielding: HTTP streaming
                        # can advance this generator from a different task.
                        try:
                            remaining = started + 20 - time.monotonic()
                            if remaining <= 0:
                                raise asyncio.TimeoutError()
                            async with asyncio.timeout(remaining):
                                part = await anext(source)
                        except StopAsyncIteration:
                            break
                        if part["type"] != "audio" or not part.get("data"):
                            continue
                        first = first or time.monotonic()
                        size += len(part["data"])
                        if size > 2 * 1024 * 1024:
                            raise RuntimeError("tts_unavailable")
                        yield part["data"]
                if not size:
                    raise edge_tts.exceptions.NoAudioReceived("empty_audio")
                break
            except (edge_tts.exceptions.NoAudioReceived, edge_tts.exceptions.WebSocketError,
                    ClientConnectionError, ClientResponseError, asyncio.TimeoutError) as exc:
                # Retry only before any audio, with the same speaker/deadline.
                if (attempt or size or isinstance(exc, ClientResponseError)
                        and exc.status not in {408, 500, 502, 503, 504}):
                    raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOG.warning("VELIA_WEB_TTS_FAILED provider=edge voice=%s kind=%s attempts=%d elapsed_ms=%d",
            fields["edge_voice"], type(exc).__name__, attempts, round((time.monotonic()-started)*1000))
        raise RuntimeError("tts_unavailable") from None
    LOG.info("VELIA_WEB_TTS_TIMING provider=edge voice=%s first_audio_ms=%d total_ms=%d attempts=%d",
        fields["edge_voice"], round((first-started)*1000), round((time.monotonic()-started)*1000), attempts)

async def synthesize_edge(fields):
    return b"".join([chunk async for chunk in stream_edge(fields)])

async def _next_audio(source, request):
    task = asyncio.create_task(anext(source))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.25)
            if request.transport is None or request.transport.is_closing():
                raise ConnectionResetError()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

async def stream_speech(request, fields):
    response = None
    try:
        async with aclosing(stream_edge(fields)) as source:
            # Validate the first provider bytes before committing HTTP 200.
            first = await _next_audio(source, request)
            response = web.StreamResponse(headers={"Content-Type": "audio/mpeg",
                "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Velia-Audio-Stream": "1"})
            await response.prepare(request)
            await response.write(first)
            while True:
                try:
                    chunk = await _next_audio(source, request)
                except StopAsyncIteration:
                    break
                await response.write(chunk)
            await response.write_eof()
            return response
    except (ClientError, RuntimeError, OSError, asyncio.TimeoutError):
        if response is None or not response.prepared:
            raise
        # A partial MP3 must not masquerade as a completed HTTP response.
        response.force_close()
        if request.transport:
            request.transport.close()
        return response

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
                LOG.info("VELIA_WEB_TTS_DELIVERY mode=%s client=%s",
                    "progressive" if request.query.get("stream") == "1" and "edge_voice" in fields else "buffered",
                    "playback-clock-v1" if request.headers.get("X-Velia-Voice-Client") == "playback-clock-v1" else "legacy")
                if request.query.get("stream") == "1" and "edge_voice" in fields:
                    return await stream_speech(request, fields)
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
