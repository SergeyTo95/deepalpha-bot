"""Azure Speech REST adapter. Opt-in, F0 only, no paid fallback/downloads."""
from __future__ import annotations

import io
import json
import math
import os
import re
import threading
import time
import wave
from xml.sax.saxutils import escape, quoteattr

import requests

from services.velia_free_api_budget_service import FreeAPIError, bounded_limit, reserve

_SLOTS = threading.BoundedSemaphore(2)
_VOICE_LOCK = threading.Lock()
_VOICES = {}
_MAX_AUDIO_BYTES = 2 * 1024 * 1024


def configuration():
    enabled = os.getenv("VELIA_AZURE_SPEECH_ENABLED", "false").lower() in {"true", "1", "on"}
    key = os.getenv("AZURE_SPEECH_KEY", "").strip()
    region = os.getenv("AZURE_SPEECH_REGION", "").strip().lower()
    tier = os.getenv("AZURE_SPEECH_TIER", "").strip().upper()
    ready = enabled and bool(key) and bool(re.fullmatch(r"[a-z][a-z0-9]{1,30}", region)) and tier == "F0"
    return {"enabled": enabled, "configured": ready, "tier": "F0", "paid_fallback": False,
            "account_plan_verified": False,
            "reason": "configured" if ready else "requires_enabled_f0_resource_key_and_region"}


def _credentials():
    if not configuration()["configured"]:
        raise FreeAPIError("speech_not_configured")
    return os.environ["AZURE_SPEECH_KEY"].strip(), os.environ["AZURE_SPEECH_REGION"].strip().lower()


def _request(url, *, key, body=None, headers=None, params=None, max_bytes=512 * 1024):
    if not _SLOTS.acquire(blocking=False):
        raise FreeAPIError("speech_busy", 429)
    try:
        request = requests.get if body is None else requests.post
        options = {} if body is None else {"data": body}
        started = time.monotonic()
        with request(url, **options, params=params,
                     headers={"Ocp-Apim-Subscription-Key": key, **(headers or {})},
                     timeout=(4, 25), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise FreeAPIError("speech_provider_unavailable", 503)
            chunks, size = [], 0
            for chunk in response.iter_content(32768):
                size += len(chunk)
                if size > max_bytes or time.monotonic() - started > 30:
                    raise FreeAPIError("speech_response_too_large", 502)
                chunks.append(chunk)
            return b"".join(chunks)
    except requests.RequestException:
        raise FreeAPIError("speech_provider_unavailable") from None
    finally:
        _SLOTS.release()


def voices():
    key, region = _credentials()
    with _VOICE_LOCK:
        cached = _VOICES.get(region)
        if cached and cached[0] > time.monotonic():
            return [dict(row) for row in cached[1]]
        try:
            payload = json.loads(_request(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list", key=key))
            if not isinstance(payload, list):
                raise ValueError()
            rows = [{"name": r["ShortName"], "locale": r["Locale"], "gender": r.get("Gender", "")}
                    for r in payload if isinstance(r, dict) and r.get("VoiceType") == "Neural"
                    and re.fullmatch(r"[A-Za-z0-9-]{3,100}", str(r.get("ShortName", "")))
                    and re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){1,3}", str(r.get("Locale", "")))]
        except (ValueError, KeyError, TypeError):
            raise FreeAPIError("speech_invalid_response", 502) from None
        _VOICES[region] = (time.monotonic() + 3600, rows)
        return [dict(row) for row in rows]


def synthesize(text, voice, *, user_id):
    key, region = _credentials()
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise FreeAPIError("speech_text_invalid", 400)
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise FreeAPIError("speech_text_invalid", 400)
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise FreeAPIError("speech_text_invalid", 400) from None
    selected = next((row for row in voices() if row["name"] == voice), None)
    if not selected:
        raise FreeAPIError("speech_voice_unsupported", 400)
    body = (f'<speak version="1.0" xml:lang={quoteattr(selected["locale"])}>'
            f'<voice name={quoteattr(selected["name"])}>{escape(text)}</voice></speak>').encode("utf-8")
    # Count UTF-16 units conservatively for supplementary characters too.
    units = len(text.encode("utf-16-le")) // 2
    reserve("azure-tts", key, units,
            bounded_limit("VELIA_AZURE_TTS_MONTHLY_CHARACTERS", 450000, 450000),
            user_id=int(user_id), daily_user_limit=10000)
    return _request(f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", key=key, body=body,
                    headers={"Content-Type": "application/ssml+xml", "User-Agent": "Velia",
                             "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3"},
                    max_bytes=_MAX_AUDIO_BYTES)


def transcribe(raw, language, *, user_id):
    key, region = _credentials()
    if not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){1,3}", language):
        raise FreeAPIError("speech_language_invalid", 400)
    # Short-audio REST requires explicit locale. Do not silently force RU/EN/TR.
    if not isinstance(raw, bytes) or len(raw) > _MAX_AUDIO_BYTES:
        raise FreeAPIError("speech_audio_invalid", 400)
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            frames = audio.getnframes()
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, 16000, "NONE"):
                raise ValueError()
            seconds = frames / 16000
            if not 0 < seconds <= 60 or len(audio.readframes(frames)) != frames * 2:
                raise ValueError()
    except (wave.Error, EOFError, ValueError):
        raise FreeAPIError("speech_requires_pcm16_mono_16khz_wav_up_to_60s", 400) from None
    reserve("azure-stt", key, math.ceil(seconds),
            bounded_limit("VELIA_AZURE_STT_MONTHLY_SECONDS", 14400, 14400),
            user_id=int(user_id), daily_user_limit=300)
    data = _request(f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1",
                    key=key, body=raw, headers={"Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                                              "Accept": "application/json"},
                    params={"language": language, "format": "simple"})
    try:
        payload = json.loads(data)
        status = payload.get("RecognitionStatus")
        if status == "Success" and isinstance(payload.get("DisplayText"), str):
            return {"text": payload["DisplayText"][:12000], "language": language}
        if status in {"NoMatch", "InitialSilenceTimeout", "BabbleTimeout"}:
            return {"text": "", "language": language}
    except (ValueError, AttributeError):
        pass
    raise FreeAPIError("speech_invalid_response", 502)
