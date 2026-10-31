"""Bounded, opt-in transcription using the existing free Groq account only."""
import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from aiohttp import ClientSession, ClientTimeout, ClientError, FormData, web
from velia_desktop_routes import AuthenticationUnavailable

LOG = logging.getLogger(__name__)
MAX_AUDIO = 1024 * 1024
TYPES = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg"}

class TranscriptionLimited(RuntimeError):
    pass

def enabled():
    return bool(os.getenv("GROQ_API_KEY")) and os.getenv("VELIA_VOICE_GROQ_FREE_ACCOUNT", "").lower() in {"true", "1"}

def audio_type(data, mime):
    mime = mime.split(";", 1)[0].lower()
    if mime == "audio/webm" and data.startswith(b"\x1aE\xdf\xa3"):
        return mime
    if mime == "audio/mp4" and len(data) >= 12 and data[4:8] == b"ftyp":
        return mime
    if mime == "audio/ogg" and data.startswith(b"OggS"):
        return mime
    raise ValueError("invalid_audio")

async def transcribe(data, mime, language):
    form = FormData()
    form.add_field("file", data, filename="utterance." + TYPES[mime], content_type=mime)
    form.add_field("model", "whisper-large-v3-turbo")
    form.add_field("response_format", "json")
    form.add_field("temperature", "0")
    form.add_field("language", language)
    async with ClientSession(timeout=ClientTimeout(total=15, connect=4)) as client:
        async with client.post("https://api.groq.com/openai/v1/audio/transcriptions", data=form,
                headers={"Authorization": "Bearer " + os.environ["GROQ_API_KEY"]}, allow_redirects=False) as response:
            if response.status == 429:
                raise TranscriptionLimited()
            if response.status != 200:
                raise RuntimeError("stt_unavailable")
            raw = bytearray()
            async for part in response.content.iter_chunked(8192):
                raw.extend(part)
                if len(raw) > 65536:
                    raise RuntimeError("stt_invalid_response")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise RuntimeError("stt_invalid_response") from None
            text = result.get("text") if isinstance(result, dict) else None
            if not isinstance(text, str) or len(text) > 12000:
                raise RuntimeError("stt_invalid_response")
            return text.strip()

def setup_transcription(app, *, session_for, same_origin, json_response):
    histories = {}
    slots = asyncio.Semaphore(2)
    async def recognize(request):
        if not same_origin(request):
            return json_response({"error": "invalid_origin"}, 403)
        try:
            session = await session_for(request)
            if not session:
                return json_response({"error": "unauthorized"}, 401)
            if not enabled():
                return json_response({"error": "stt_not_configured"}, 503)
            language = request.query.get("language", "")
            if not re.fullmatch(r"[a-z]{2}", language) or request.content_type not in TYPES:
                return json_response({"error": "invalid_audio"}, 400)
            now = time.monotonic()
            for owner in list(histories):
                if histories[owner][-1] <= now - 60:
                    del histories[owner]
            history = histories.setdefault(session.user_id, deque())
            while history and history[0] <= now - 60:
                history.popleft()
            if slots.locked() or len(history) >= 8:
                return json_response({"error": "stt_rate_limit"}, 429)
            history.append(now)
            async with slots, asyncio.timeout(20):
                # Streaming reads enforce our route-specific limit without
                # increasing the gateway's small authentication JSON limit.
                data = bytearray()
                async for part in request.content.iter_chunked(65536):
                    data.extend(part)
                    if len(data) > MAX_AUDIO:
                        return json_response({"error": "request_too_large"}, 413)
                mime = audio_type(data, request.content_type)
                text = await transcribe(bytes(data), mime, language)
            LOG.info("VELIA_VOICE_STT provider=groq total_ms=%d", round((time.monotonic()-now)*1000))
            return json_response({"text": text})
        except AuthenticationUnavailable:
            return json_response({"error": "authentication_unavailable"}, 503)
        except (ValueError, UnicodeDecodeError):
            return json_response({"error": "invalid_audio"}, 400)
        except TranscriptionLimited:
            return json_response({"error": "stt_rate_limit"}, 429)
        except (ClientError, TimeoutError, RuntimeError):
            return json_response({"error": "stt_unavailable"}, 503)
    app.router.add_post("/web-api/v1/voice/transcribe", recognize)
