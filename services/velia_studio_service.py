import hashlib
import hmac
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from PIL import Image

from db.database import get_connection
from services.velia_images_service import generate_and_store_image, image_metadata_for_request
from services.velia_music_service import ensure_velia_music_tables, music_metadata_for_request
from services.velia_videos_service import (
    RequestImageAttachment,
    VideoGenerationError,
    _env_bool as _video_env_bool,
    _release_capacity_reservation as _release_video_capacity,
    _reserve_capacity as _reserve_video_capacity,
    _submit_and_wait as _submit_video_and_wait,
    video_metadata_for_request,
)

logger = logging.getLogger(__name__)
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_ALLOWED_MODES = {"image", "video", "music"}
_ALLOWED_MIME = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
_MAX_REFERENCE_BYTES = 15 * 1024 * 1024
_MAX_REFERENCE_PIXELS = 36_000_000
_MAX_REFERENCES = 4
_MAX_PROMPT_CHARS = 4000


class StudioError(ValueError):
    def __init__(self, code: str, *, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def studio_enabled() -> bool:
    return _env_bool("VELIA_STUDIO_ENABLED", False)


def studio_music_enabled() -> bool:
    return _env_bool("VELIA_STUDIO_MUSIC_ENABLED", False)


def _rv(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def ensure_velia_studio_tables() -> None:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velia_studio_sessions (
                session_id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('image','video','music')),
                title TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                archived_at TIMESTAMP NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velia_studio_generations (
                generation_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES velia_studio_sessions(session_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                client_request_id TEXT NOT NULL,
                generation_type TEXT NOT NULL CHECK (generation_type IN ('image','video','music')),
                prompt TEXT NOT NULL,
                reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','error')),
                output_request_id TEXT NULL,
                estimated_cost_usd NUMERIC(18,8) NOT NULL DEFAULT 0,
                error_code TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMP NULL,
                UNIQUE (user_id, client_request_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velia_studio_messages (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES velia_studio_sessions(session_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user','assistant')),
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN ('completed','error')),
                generation_id TEXT NULL REFERENCES velia_studio_generations(generation_id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        # Durable worker orchestration metadata. ADD COLUMN is intentionally
        # idempotent because Studio schema bootstrap runs inside the app.
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS duration_seconds INTEGER NOT NULL DEFAULT 5")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS worker_job_id TEXT NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS worker_status TEXT NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS worker_reservation_id TEXT NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS estimated_seconds_remaining INTEGER NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS estimated_completion_at TIMESTAMP NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS worker_updated_at TIMESTAMP NULL")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS lyrics TEXT NOT NULL DEFAULT ''")
        cur.execute("ALTER TABLE velia_studio_generations ADD COLUMN IF NOT EXISTS instrumental BOOLEAN NOT NULL DEFAULT FALSE")
        # Existing production tables were created before Music mode existed.
        # Replace only the two generated CHECK constraints when their current
        # definition does not yet include music.
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='velia_studio_sessions_mode_check'
                      AND pg_get_constraintdef(oid) LIKE '%music%'
                ) THEN
                    ALTER TABLE velia_studio_sessions
                        DROP CONSTRAINT IF EXISTS velia_studio_sessions_mode_check;
                    ALTER TABLE velia_studio_sessions
                        ADD CONSTRAINT velia_studio_sessions_mode_check
                        CHECK (mode IN ('image','video','music'));
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname='velia_studio_generations_generation_type_check'
                      AND pg_get_constraintdef(oid) LIKE '%music%'
                ) THEN
                    ALTER TABLE velia_studio_generations
                        DROP CONSTRAINT IF EXISTS velia_studio_generations_generation_type_check;
                    ALTER TABLE velia_studio_generations
                        ADD CONSTRAINT velia_studio_generations_generation_type_check
                        CHECK (generation_type IN ('image','video','music'));
                END IF;
            END $$
            """
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS velia_studio_assets (
                asset_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES velia_studio_sessions(session_id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                original_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size BIGINT NOT NULL,
                sha256 TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                content_bytes BYTEA NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_studio_sessions_user_mode ON velia_studio_sessions(user_id,mode,updated_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_studio_messages_session ON velia_studio_messages(session_id,created_at ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_studio_assets_session ON velia_studio_assets(session_id,created_at ASC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_studio_pending_worker ON velia_studio_generations(status,worker_status,worker_updated_at)")
        ensure_velia_music_tables(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    initialized = False
    with _SCHEMA_LOCK:
        if not _SCHEMA_READY:
            ensure_velia_studio_tables()
            _SCHEMA_READY = True
            initialized = True
    if initialized:
        try:
            from services.velia_studio_video_worker_service import (
                resume_pending_self_hosted_video_monitors,
            )

            resume_pending_self_hosted_video_monitors()
        except Exception as exc:
            logger.exception(
                "VELIA_STUDIO_VIDEO_RESUME_FAILED error_type=%s",
                type(exc).__name__,
            )
        try:
            from services.velia_studio_music_worker_service import (
                resume_pending_self_hosted_music_monitors,
            )

            resume_pending_self_hosted_music_monitors()
        except Exception as exc:
            logger.exception(
                "VELIA_STUDIO_MUSIC_RESUME_FAILED error_type=%s",
                type(exc).__name__,
            )


def _mode(value: str) -> str:
    value = str(value or "").strip().lower()
    if value not in _ALLOWED_MODES:
        raise StudioError("studio_invalid_mode")
    return value


def _prompt(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if not value:
        raise StudioError("studio_prompt_required")
    if len(value) > _MAX_PROMPT_CHARS:
        raise StudioError("studio_prompt_too_long", status=413)
    return value


def _reference_ids(values: Any) -> List[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise StudioError("studio_invalid_reference_ids")
    result = []
    for value in values:
        try:
            normalized = str(uuid.UUID(str(value or "").strip()))
        except (ValueError, TypeError, AttributeError):
            raise StudioError("studio_invalid_reference_id")
        if normalized not in result:
            result.append(normalized)
    if len(result) > _MAX_REFERENCES:
        raise StudioError("studio_too_many_references", status=413)
    return result


def _auto_title(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt).strip()[:90].rstrip(" ,.;:-") or "Studio"


def create_session(user_id: int, mode: str, title: str = "") -> Dict[str, Any]:
    _ensure_schema()
    mode = _mode(mode)
    if mode == "music" and not studio_music_enabled():
        raise StudioError("studio_music_disabled", status=503)
    now = datetime.utcnow()
    session_id = str(uuid.uuid4())
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:90]
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO velia_studio_sessions(session_id,user_id,mode,title,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s)", (session_id,int(user_id),mode,title,now,now))
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()
    return {"id": session_id, "mode": mode, "title": title, "created_at": _iso(now), "updated_at": _iso(now)}


def get_session(user_id: int, session_id: str) -> Optional[Dict[str, Any]]:
    _ensure_schema()
    conn = get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT session_id,mode,title,created_at,updated_at FROM velia_studio_sessions WHERE session_id=%s AND user_id=%s AND archived_at IS NULL LIMIT 1", (str(session_id),int(user_id)))
        row = cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row:
        return None
    return {"id": str(_rv(row,"session_id",0,"")), "mode": str(_rv(row,"mode",1,"")), "title": str(_rv(row,"title",2,"")), "created_at": _iso(_rv(row,"created_at",3)), "updated_at": _iso(_rv(row,"updated_at",4))}


def list_sessions(user_id: int, *, mode: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    _ensure_schema()
    mode = _mode(mode) if mode else None
    limit = max(1, min(int(limit or 100), 200))
    conn = get_connection(); cur = conn.cursor()
    try:
        if mode:
            cur.execute("SELECT session_id,mode,title,created_at,updated_at FROM velia_studio_sessions WHERE user_id=%s AND mode=%s AND archived_at IS NULL ORDER BY updated_at DESC LIMIT %s", (int(user_id),mode,limit))
        else:
            cur.execute("SELECT session_id,mode,title,created_at,updated_at FROM velia_studio_sessions WHERE user_id=%s AND archived_at IS NULL ORDER BY updated_at DESC LIMIT %s", (int(user_id),limit))
        rows = cur.fetchall() or []
    finally:
        cur.close(); conn.close()
    return [{"id": str(_rv(r,"session_id",0,"")), "mode": str(_rv(r,"mode",1,"")), "title": str(_rv(r,"title",2,"")), "created_at": _iso(_rv(r,"created_at",3)), "updated_at": _iso(_rv(r,"updated_at",4))} for r in rows]


def _verify_image(raw: bytes, mime_type: str) -> tuple[int,int]:
    mime_type = str(mime_type or "").split(";",1)[0].strip().lower()
    expected = _ALLOWED_MIME.get(mime_type)
    if not expected:
        raise StudioError("studio_reference_type_not_supported", status=415)
    if not raw:
        raise StudioError("studio_reference_empty")
    if len(raw) > _MAX_REFERENCE_BYTES:
        raise StudioError("studio_reference_too_large", status=413)
    try:
        with Image.open(io.BytesIO(raw)) as image: image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            actual = str(image.format or "").upper(); width,height = image.size
    except Exception as exc:
        raise StudioError("studio_reference_invalid", status=415) from exc
    if actual != expected:
        raise StudioError("studio_reference_type_mismatch", status=415)
    if width <= 0 or height <= 0 or width * height > _MAX_REFERENCE_PIXELS:
        raise StudioError("studio_reference_dimensions_rejected", status=413)
    return int(width), int(height)


def _signing_secret() -> bytes:
    secret = str(os.getenv("VELIA_STUDIO_SIGNING_SECRET","") or os.getenv("VELYON_IMAGES_SIGNING_SECRET","") or os.getenv("VELYON_IMAGES_API_KEY","")).strip()
    if not secret:
        raise RuntimeError("studio_signing_secret_missing")
    return hashlib.sha256((secret+":velia-studio").encode()).digest()


def sign_reference_url(asset_id: str, user_id: int, expires_at: int) -> str:
    return hmac.new(_signing_secret(), f"{asset_id}:{int(user_id)}:{int(expires_at)}".encode(), hashlib.sha256).hexdigest()


def verify_reference_signature(asset_id: str, user_id: int, expires_at: int, signature: str) -> bool:
    if int(expires_at) < int(time.time()):
        return False
    try:
        expected = sign_reference_url(asset_id,user_id,expires_at)
    except Exception:
        return False
    return hmac.compare_digest(expected,str(signature or ""))


def reference_asset_metadata(asset_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    _ensure_schema()
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("SELECT asset_id,original_name,mime_type,byte_size,width,height,created_at FROM velia_studio_assets WHERE asset_id=%s AND user_id=%s LIMIT 1", (str(asset_id),int(user_id)))
        row=cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row:
        return None
    expires=int(time.time())+86400
    signature=sign_reference_url(asset_id,user_id,expires)
    return {"id":str(_rv(row,"asset_id",0,"")),"kind":"reference_image","name":str(_rv(row,"original_name",1,"")),"mime_type":str(_rv(row,"mime_type",2,"image/jpeg")),"byte_size":int(_rv(row,"byte_size",3,0) or 0),"width":int(_rv(row,"width",4,0) or 0),"height":int(_rv(row,"height",5,0) or 0),"content_url":f"/api/mobile/studio/assets/{asset_id}/content?user_id={int(user_id)}&expires={expires}&signature={signature}","created_at":_iso(_rv(row,"created_at",6))}


def create_reference_asset(user_id: int, session_id: str, *, filename: str, mime_type: str, content: bytes) -> Dict[str, Any]:
    _ensure_schema()
    if not get_session(user_id,session_id):
        raise StudioError("studio_session_not_found", status=404)
    raw=bytes(content or b""); mime_type=str(mime_type or "").split(";",1)[0].strip().lower(); width,height=_verify_image(raw,mime_type)
    asset_id=str(uuid.uuid4()); name=re.sub(r"[\x00-\x1f\x7f]+","",str(filename or "reference")).strip()[:180] or "reference"
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM velia_studio_assets WHERE session_id=%s AND user_id=%s",(str(session_id),int(user_id)))
        if int((cur.fetchone() or (0,))[0] or 0) >= 50: raise StudioError("studio_reference_session_limit",status=429)
        cur.execute("INSERT INTO velia_studio_assets(asset_id,session_id,user_id,original_name,mime_type,byte_size,sha256,width,height,content_bytes,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(asset_id,str(session_id),int(user_id),name,mime_type,len(raw),hashlib.sha256(raw).hexdigest(),width,height,raw,datetime.utcnow()))
        cur.execute("UPDATE velia_studio_sessions SET updated_at=NOW() WHERE session_id=%s AND user_id=%s",(str(session_id),int(user_id)))
        conn.commit()
    except StudioError:
        conn.rollback(); raise
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()
    return reference_asset_metadata(asset_id,user_id) or {}


def get_reference_content(asset_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    _ensure_schema(); conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("SELECT content_bytes,mime_type FROM velia_studio_assets WHERE asset_id=%s AND user_id=%s LIMIT 1",(str(asset_id),int(user_id))); row=cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row: return None
    raw=bytes(_rv(row,"content_bytes",0,b"") or b"")
    return {"bytes":raw,"mime_type":str(_rv(row,"mime_type",1,"image/jpeg"))} if raw else None


def _load_refs(user_id: int, session_id: str, ids: List[str]) -> List[Dict[str, Any]]:
    if not ids: return []
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("SELECT asset_id,mime_type,content_bytes,width,height FROM velia_studio_assets WHERE user_id=%s AND session_id=%s AND asset_id=ANY(%s)",(int(user_id),str(session_id),ids)); rows=cur.fetchall() or []
    finally:
        cur.close(); conn.close()
    by_id={str(_rv(r,"asset_id",0,"")):r for r in rows}
    if set(by_id)!=set(ids): raise StudioError("studio_reference_not_found",status=404)
    return [{"id":i,"mime_type":str(_rv(by_id[i],"mime_type",1,"")),"content_bytes":bytes(_rv(by_id[i],"content_bytes",2,b"") or b""),"width":int(_rv(by_id[i],"width",3,0) or 0),"height":int(_rv(by_id[i],"height",4,0) or 0)} for i in ids]


def _generation(user_id: int, *, generation_id: Optional[str]=None, client_request_id: Optional[str]=None) -> Optional[Dict[str, Any]]:
    conn=get_connection(); cur=conn.cursor()
    try:
        if generation_id:
            cur.execute("SELECT generation_id,session_id,generation_type,prompt,reference_asset_ids_json,status,output_request_id,estimated_cost_usd,error_code,created_at,completed_at,client_request_id,duration_seconds,worker_status,progress_percent,estimated_seconds_remaining,estimated_completion_at,lyrics,instrumental FROM velia_studio_generations WHERE generation_id=%s AND user_id=%s LIMIT 1",(generation_id,int(user_id)))
        else:
            cur.execute("SELECT generation_id,session_id,generation_type,prompt,reference_asset_ids_json,status,output_request_id,estimated_cost_usd,error_code,created_at,completed_at,client_request_id,duration_seconds,worker_status,progress_percent,estimated_seconds_remaining,estimated_completion_at,lyrics,instrumental FROM velia_studio_generations WHERE client_request_id=%s AND user_id=%s LIMIT 1",(str(client_request_id),int(user_id)))
        row=cur.fetchone()
    finally:
        cur.close(); conn.close()
    if not row: return None
    gen_id=str(_rv(row,"generation_id",0,"")); gen_type=str(_rv(row,"generation_type",2,"")); out=str(_rv(row,"output_request_id",6,"") or "")
    try: ref_ids=json.loads(str(_rv(row,"reference_asset_ids_json",4,"[]") or "[]"))
    except Exception: ref_ids=[]
    media=image_metadata_for_request(out,int(user_id)) if out and gen_type=="image" else video_metadata_for_request(out,int(user_id)) if out and gen_type=="video" else music_metadata_for_request(out,int(user_id)) if out and gen_type=="music" else None
    refs=[m for m in (reference_asset_metadata(str(i),user_id) for i in ref_ids) if m]
    return {"id":gen_id,"session_id":str(_rv(row,"session_id",1,"")),"type":gen_type,"prompt":str(_rv(row,"prompt",3,"")),"client_request_id":str(_rv(row,"client_request_id",11,"") or ""),"references":refs,"status":str(_rv(row,"status",5,"pending")),"media":media,"estimated_cost_usd":float(_rv(row,"estimated_cost_usd",7,0) or 0),"error_code":_rv(row,"error_code",8),"duration_seconds":int(_rv(row,"duration_seconds",12,5) or 5),"worker_status":str(_rv(row,"worker_status",13,"") or "") or None,"progress_percent":max(0,min(100,int(_rv(row,"progress_percent",14,0) or 0))),"estimated_seconds_remaining":int(_rv(row,"estimated_seconds_remaining",15,0)) if _rv(row,"estimated_seconds_remaining",15) is not None else None,"estimated_completion_at":_iso(_rv(row,"estimated_completion_at",16)),"lyrics":str(_rv(row,"lyrics",17,"") or ""),"instrumental":bool(_rv(row,"instrumental",18,False)),"created_at":_iso(_rv(row,"created_at",9)),"completed_at":_iso(_rv(row,"completed_at",10))}


def list_messages(user_id: int, session_id: str, *, limit: int=200) -> List[Dict[str, Any]]:
    _ensure_schema()
    if not get_session(user_id,session_id): raise StudioError("studio_session_not_found",status=404)
    conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("SELECT message_id,role,content,status,generation_id,created_at FROM velia_studio_messages WHERE session_id=%s AND user_id=%s ORDER BY created_at ASC,message_id ASC LIMIT %s",(str(session_id),int(user_id),max(1,min(int(limit or 200),400)))); rows=cur.fetchall() or []
    finally:
        cur.close(); conn.close()
    return [{"id":str(_rv(r,"message_id",0,"")),"role":str(_rv(r,"role",1,"")),"content":str(_rv(r,"content",2,"")),"status":str(_rv(r,"status",3,"completed")),"generation":_generation(user_id,generation_id=str(_rv(r,"generation_id",4,"") or "")) if _rv(r,"generation_id",4) else None,"created_at":_iso(_rv(r,"created_at",5))} for r in rows]


def _insert_turn(user_id: int, session_id: str, mode: str, prompt: str, client_request_id: str, refs: List[str], *, duration_seconds: int = 5, lyrics: str = "", instrumental: bool = False) -> str:
    generation_id=str(uuid.uuid4()); now=datetime.utcnow(); message_id=str(uuid.uuid4()); conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("INSERT INTO velia_studio_generations(generation_id,session_id,user_id,client_request_id,generation_type,prompt,reference_asset_ids_json,status,duration_seconds,lyrics,instrumental,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s)",(generation_id,str(session_id),int(user_id),client_request_id,mode,prompt,json.dumps(refs),int(duration_seconds),str(lyrics),bool(instrumental),now))
        cur.execute("INSERT INTO velia_studio_messages(message_id,session_id,user_id,role,content,status,generation_id,created_at) VALUES(%s,%s,%s,'user',%s,'completed',%s,%s)",(message_id,str(session_id),int(user_id),prompt,generation_id,now))
        cur.execute("UPDATE velia_studio_sessions SET title=CASE WHEN title='' THEN %s ELSE title END,updated_at=%s WHERE session_id=%s AND user_id=%s",(_auto_title(prompt),now,str(session_id),int(user_id))); conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()
    return generation_id


def _finish(user_id: int, session_id: str, generation_id: str, *, created: bool, cost: float, error_code: Optional[str], text: str) -> None:
    now=datetime.utcnow(); conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("UPDATE velia_studio_generations SET status=%s,output_request_id=%s,estimated_cost_usd=%s,error_code=%s,completed_at=%s WHERE generation_id=%s AND user_id=%s",("completed" if created else "error",generation_id if created else None,float(cost or 0),error_code,now,generation_id,int(user_id)))
        cur.execute("INSERT INTO velia_studio_messages(message_id,session_id,user_id,role,content,status,generation_id,created_at) VALUES(%s,%s,%s,'assistant',%s,%s,%s,%s)",(str(uuid.uuid4()),str(session_id),int(user_id),text,"completed" if created else "error",generation_id,now))
        cur.execute("UPDATE velia_studio_sessions SET updated_at=%s WHERE session_id=%s AND user_id=%s",(now,str(session_id),int(user_id))); conn.commit()
    except Exception:
        conn.rollback(); raise
    finally:
        cur.close(); conn.close()


def _studio_video(user_id: int, session_id: str, generation_id: str, prompt: str, ref: Optional[Dict[str,Any]]) -> tuple[bool,float,Optional[str]]:
    if not _video_env_bool("VELYON_VIDEOS_ENABLED",False): return False,0.0,"video_service_disabled"
    try: limit_error,reservation_id=_reserve_video_capacity(int(user_id))
    except Exception: return False,0.0,"video_capacity_unavailable"
    if limit_error: return False,0.0,limit_error
    attachment=RequestImageAttachment(str(ref["id"]),str(ref["mime_type"]),bytes(ref["content_bytes"]),int(ref["width"]),int(ref["height"])) if ref else None
    try: generated=_submit_video_and_wait(mode="i2v" if attachment else "t2v",prompt=prompt,attachment=attachment)
    except VideoGenerationError as exc:
        _release_video_capacity(reservation_id); return False,0.0,exc.code
    except Exception:
        _release_video_capacity(reservation_id); return False,0.0,"video_generation_failed"
    raw=bytes(generated["video_bytes"]); cost=float(generated["estimated_cost_usd"]); conn=get_connection(); cur=conn.cursor()
    try:
        cur.execute("INSERT INTO velia_generated_videos(video_id,user_id,conversation_id,request_id,prompt,mode,mime_type,byte_size,duration_seconds,resolution,aspect_ratio,has_audio,video_bytes,external_request_id,estimated_cost_usd,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(str(uuid.uuid4()),int(user_id),f"studio:{session_id}",generation_id,prompt,"i2v" if attachment else "t2v",str(generated["mime_type"]),len(raw),int(generated["duration_seconds"]),str(generated["resolution"]),str(generated["aspect_ratio"]),bool(generated["has_audio"]),raw,str(generated.get("external_request_id") or "")[:200],cost,datetime.utcnow()))
        cur.execute("DELETE FROM velia_video_reservations WHERE reservation_id=%s",(str(reservation_id),)); conn.commit()
    except Exception:
        conn.rollback(); _release_video_capacity(reservation_id); return False,0.0,"video_storage_failed"
    finally:
        cur.close(); conn.close()
    return True,cost,None


def generate_turn(*, user_id: int, session_id: str, prompt: str, client_request_id: str, reference_asset_ids: Any=None) -> Dict[str,Any]:
    _ensure_schema()
    if not studio_enabled(): raise StudioError("studio_disabled",status=503)
    session=get_session(user_id,session_id)
    if not session: raise StudioError("studio_session_not_found",status=404)
    prompt=_prompt(prompt); client_request_id=str(client_request_id or "").strip()
    if not client_request_id or len(client_request_id)>200: raise StudioError("studio_invalid_idempotency_key")
    existing=_generation(user_id,client_request_id=client_request_id)
    if existing:
        if existing["session_id"]!=str(session_id): raise StudioError("studio_idempotency_conflict",status=409)
        return {"duplicate":True,"generation":existing}
    ref_ids=_reference_ids(reference_asset_ids); refs=_load_refs(user_id,session_id,ref_ids); mode=str(session["mode"])
    if mode=="video" and len(refs)>1: raise StudioError("studio_video_requires_zero_or_one_reference")
    if mode=="image" and refs: raise StudioError("studio_image_references_not_supported",status=409)
    generation_id=_insert_turn(user_id,session_id,mode,prompt,client_request_id,ref_ids)
    if mode=="image":
        result=generate_and_store_image(user_id=int(user_id),conversation_id=f"studio:{session_id}",request_id=generation_id,original_message=prompt,prompt=prompt)
        created=bool(result.get("image_created")); cost=float(result.get("estimated_cost_usd") or 0); error=None if created else "image_generation_failed"; text=str(result.get("text") or ("Изображение готово." if created else "Не удалось создать изображение."))
    else:
        created,cost,error=_studio_video(user_id,session_id,generation_id,prompt,refs[0] if refs else None); text="Видео готово." if created else "Не удалось создать видео."
    _finish(user_id,session_id,generation_id,created=created,cost=cost,error_code=error,text=text)
    return {"duplicate":False,"generation":_generation(user_id,generation_id=generation_id)}
