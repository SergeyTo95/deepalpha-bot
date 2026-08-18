from __future__ import annotations

import hashlib
import hmac
import io
import os
import time
import uuid
import wave
from datetime import datetime
from typing import Any, Dict, Optional

from db.database import get_connection


MUSIC_ATTRIBUTION = "MiniMax-Music3 · AI-generated"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def ensure_velia_music_tables(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS velia_generated_music (
            music_id TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            conversation_id TEXT NOT NULL,
            request_id TEXT NOT NULL UNIQUE,
            prompt TEXT NOT NULL,
            lyrics TEXT NOT NULL DEFAULT '',
            instrumental BOOLEAN NOT NULL DEFAULT FALSE,
            mime_type TEXT NOT NULL,
            byte_size BIGINT NOT NULL CHECK (byte_size > 0),
            sha256 TEXT NOT NULL,
            duration_seconds NUMERIC(10,3) NOT NULL CHECK (duration_seconds > 0),
            sample_rate_hz INTEGER NOT NULL CHECK (sample_rate_hz > 0),
            channels INTEGER NOT NULL CHECK (channels > 0),
            audio_bytes BYTEA NOT NULL,
            external_request_id TEXT NULL,
            estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_velia_generated_music_user_created "
        "ON velia_generated_music(user_id,created_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_velia_generated_music_conversation "
        "ON velia_generated_music(conversation_id,created_at ASC)"
    )


def _signing_secret() -> bytes:
    configured = str(os.getenv("VELIA_STUDIO_MUSIC_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELIA_STUDIO_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_VIDEOS_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_IMAGES_SIGNING_SECRET", "") or "").strip()
    if not configured:
        raise RuntimeError("music_signing_secret_missing")
    return hashlib.sha256((configured + ":velia-music").encode("utf-8")).digest()


def sign_music_url(music_id: str, user_id: int, expires_at: int) -> str:
    payload = f"{music_id}:{int(user_id)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(_signing_secret(), payload, hashlib.sha256).hexdigest()


def verify_music_signature(
    music_id: str,
    user_id: int,
    expires_at: int,
    signature: str,
) -> bool:
    if int(expires_at) < int(time.time()):
        return False
    try:
        expected = sign_music_url(music_id, user_id, expires_at)
    except (RuntimeError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(signature or ""))


def inspect_music_wav(content: bytes) -> Dict[str, Any]:
    raw = bytes(content or b"")
    max_bytes = _env_int(
        "VELIA_STUDIO_MUSIC_MAX_BYTES",
        64 * 1024 * 1024,
        1 * 1024 * 1024,
        128 * 1024 * 1024,
    )
    if len(raw) < 44 or len(raw) > max_bytes:
        raise ValueError("music_artifact_size_invalid")
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("music_artifact_not_wav")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            channels = int(wav.getnchannels())
            sample_rate = int(wav.getframerate())
            sample_width = int(wav.getsampwidth())
            frames = int(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("music_artifact_invalid_wav") from exc
    # MiniMax Music3's native Diffusers pipeline emits 44.1 kHz stereo PCM.
    # Keep accepting the earlier 32 kHz reference-server output as well so
    # already compatible workers do not regress.
    if channels != 2 or sample_rate not in {32000, 44100} or sample_width != 2 or frames <= 0:
        raise ValueError("music_artifact_format_invalid")
    duration = frames / sample_rate
    if duration < 1.0 or duration > 305.0:
        raise ValueError("music_artifact_duration_invalid")
    return {
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "duration_seconds": round(duration, 3),
        "sample_rate_hz": sample_rate,
        "channels": channels,
    }


def store_generated_music(
    *,
    user_id: int,
    session_id: str,
    generation_id: str,
    prompt: str,
    lyrics: str,
    instrumental: bool,
    generated: Dict[str, Any],
) -> str:
    raw = bytes(generated.get("audio_bytes") or b"")
    metadata = inspect_music_wav(raw)
    declared_sha = str(generated.get("sha256") or "").strip().lower()
    if declared_sha and declared_sha != metadata["sha256"]:
        raise ValueError("music_artifact_sha256_mismatch")
    music_id = str(uuid.uuid4())
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO velia_generated_music(
                music_id,user_id,conversation_id,request_id,prompt,lyrics,
                instrumental,mime_type,byte_size,sha256,duration_seconds,
                sample_rate_hz,channels,audio_bytes,external_request_id,
                estimated_cost_usd,created_at
            ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                music_id,
                int(user_id),
                f"studio:{session_id}",
                str(generation_id),
                str(prompt),
                str(lyrics),
                bool(instrumental),
                "audio/wav",
                metadata["byte_size"],
                metadata["sha256"],
                metadata["duration_seconds"],
                metadata["sample_rate_hz"],
                metadata["channels"],
                raw,
                str(generated.get("external_request_id") or "")[:200],
                0.0,
                datetime.utcnow(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    return music_id


def music_metadata_for_request(request_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    if not request_id:
        return None
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT music_id,prompt,lyrics,instrumental,mime_type,duration_seconds,
                   sample_rate_hz,channels,byte_size,sha256
            FROM velia_generated_music
            WHERE request_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not row:
        return None
    if isinstance(row, dict):
        values = [
            row.get("music_id"), row.get("prompt"), row.get("lyrics"),
            row.get("instrumental"), row.get("mime_type"), row.get("duration_seconds"),
            row.get("sample_rate_hz"), row.get("channels"), row.get("byte_size"),
            row.get("sha256"),
        ]
    else:
        values = list(row)
    music_id = str(values[0] or "")
    expires_at = int(time.time()) + _env_int(
        "VELIA_STUDIO_MUSIC_URL_TTL_SECONDS",
        86400,
        300,
        604800,
    )
    signature = sign_music_url(music_id, user_id, expires_at)
    return {
        "id": music_id,
        "content_url": (
            f"/api/mobile/music/{music_id}/content?user_id={int(user_id)}"
            f"&expires={expires_at}&signature={signature}"
        ),
        "prompt": str(values[1] or ""),
        "lyrics": str(values[2] or ""),
        "instrumental": bool(values[3]),
        "mime_type": str(values[4] or "audio/wav"),
        "duration_seconds": float(values[5] or 0),
        "sample_rate_hz": int(values[6] or 32000),
        "channels": int(values[7] or 2),
        "byte_size": int(values[8] or 0),
        "sha256": str(values[9] or ""),
        "attribution": MUSIC_ATTRIBUTION,
    }


def get_music_content(music_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT audio_bytes,mime_type
            FROM velia_generated_music
            WHERE music_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(music_id), int(user_id)),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()
    if not row:
        return None
    raw = bytes((row.get("audio_bytes") if isinstance(row, dict) else row[0]) or b"")
    mime_type = str(
        (row.get("mime_type") if isinstance(row, dict) else row[1]) or "audio/wav"
    )
    return {"bytes": raw, "mime_type": mime_type} if raw else None
