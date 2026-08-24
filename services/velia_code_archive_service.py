import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services import velia_developer_github_service as github_service
from services import velia_developer_github_write_service as write_service
from services import velia_developer_project_service as project_service


class VeliaCodeArchiveError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_ARCHIVE_INTENT_RE = re.compile(r"(?:\bzip\b|\.zip\b|архив)", re.IGNORECASE)
_ARCHIVE_ACTION_RE = re.compile(
    r"(?:файл|код|проект|результат|скач|отправ|пришл|дай|собер|упак|"
    r"\bfiles?\b|\bcode\b|\bproject\b|\bresult\b|\bdownload\b|\bsend\b|\bbundle\b|\bpack\b)",
    re.IGNORECASE,
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def is_code_archive_request(message: str) -> bool:
    text = str(message or "").strip()
    return bool(text and _ARCHIVE_INTENT_RE.search(text) and _ARCHIVE_ACTION_RE.search(text))


def ensure_velia_code_archive_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_code_archives (
                    archive_id TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    request_id TEXT NOT NULL UNIQUE,
                    coding_job_id TEXT NOT NULL,
                    repository_full_name TEXT NOT NULL,
                    work_branch TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT 'application/zip',
                    archive_bytes BYTEA NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    sha256 TEXT NOT NULL,
                    file_count INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_velia_code_archives_owner
                ON velia_code_archives(user_id, conversation_id, created_at DESC)
                """
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[index]
    except (TypeError, IndexError):
        return default


def _latest_completed_job(user_id: int, conversation_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT job_id, project_id, work_branch, step_results_json
            FROM velia_developer_coding_jobs
            WHERE user_id=%s AND conversation_id=%s AND status='completed'
              AND work_branch<>''
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(user_id), str(conversation_id)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        raw_results = str(_row_value(row, "step_results_json", 3, "[]") or "[]")
        try:
            results = json.loads(raw_results)
        except Exception:
            results = []
        return {
            "job_id": str(_row_value(row, "job_id", 0, "") or ""),
            "project_id": str(_row_value(row, "project_id", 1, "") or ""),
            "work_branch": str(_row_value(row, "work_branch", 2, "") or ""),
            "step_results": results if isinstance(results, list) else [],
        }
    finally:
        cursor.close()
        conn.close()


def _result_paths(job: Dict[str, Any]) -> List[str]:
    maximum = _env_int("VELIA_CODE_ARCHIVE_MAX_FILES", 64, 1, 200)
    paths: List[str] = []
    seen = set()
    for result in job.get("step_results") if isinstance(job.get("step_results"), list) else []:
        if not isinstance(result, dict):
            continue
        raw_files = result.get("files") if isinstance(result.get("files"), list) else []
        for raw_path in raw_files:
            try:
                path = github_service.validate_path(str(raw_path or ""))
            except github_service.DeveloperGithubError:
                continue
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
            if len(paths) > maximum:
                raise VeliaCodeArchiveError("code_archive_too_many_files", status=413)
    if not paths:
        raise VeliaCodeArchiveError("code_archive_no_files", status=404)
    return paths


def _protected_archive_path(path: str) -> bool:
    normalized = github_service.validate_path(path)
    lowered = normalized.casefold()
    name = lowered.rsplit("/", 1)[-1]
    if name.startswith(".env"):
        return True
    blocked_names = {
        "id_rsa",
        "id_ed25519",
        "credentials.json",
        "service-account.json",
        "secrets.json",
        "secrets.yml",
        "secrets.yaml",
    }
    blocked_suffixes = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
    return name in blocked_names or name.endswith(blocked_suffixes) or "private_key" in name


def _archive_filename(repository_full_name: str, job_id: str) -> str:
    repo = str(repository_full_name or "project").rsplit("/", 1)[-1]
    safe_repo = re.sub(r"[^A-Za-z0-9._-]+", "-", repo).strip("-.")[:60] or "project"
    safe_job = re.sub(r"[^A-Za-z0-9]+", "", str(job_id or ""))[:8] or "files"
    return f"VELIA-{safe_repo}-{safe_job}.zip"


def _zip_bytes(files: List[Dict[str, str]]) -> bytes:
    maximum_total = _env_int(
        "VELIA_CODE_ARCHIVE_MAX_UNCOMPRESSED_BYTES",
        8 * 1024 * 1024,
        64 * 1024,
        25 * 1024 * 1024,
    )
    total = 0
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in sorted(files, key=lambda value: str(value.get("path") or "")):
            path = github_service.validate_path(str(item.get("path") or ""))
            raw = str(item.get("content") or "").encode("utf-8")
            total += len(raw)
            if total > maximum_total:
                raise VeliaCodeArchiveError("code_archive_uncompressed_too_large", status=413)
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    data = output.getvalue()
    maximum_zip = _env_int(
        "VELIA_CODE_ARCHIVE_MAX_BYTES",
        10 * 1024 * 1024,
        64 * 1024,
        30 * 1024 * 1024,
    )
    if len(data) > maximum_zip:
        raise VeliaCodeArchiveError("code_archive_too_large", status=413)
    return data


def _read_final_files(project: Dict[str, Any], branch: str, paths: List[str]) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for path in paths:
        if _protected_archive_path(path):
            raise VeliaCodeArchiveError("code_archive_protected_path", status=403, detail=path)
        try:
            item = write_service.read_utf8_file(project, branch, path)
        except write_service.DeveloperWriteError as exc:
            if exc.code == "github_not_found":
                # A file touched by an earlier step may have been intentionally
                # deleted by a later step. ZIP represents the final branch state.
                continue
            raise VeliaCodeArchiveError(exc.code, status=exc.status, detail=exc.detail) from exc
        files.append({"path": str(item.get("path") or path), "content": str(item.get("content") or "")})
    if not files:
        raise VeliaCodeArchiveError("code_archive_no_final_files", status=404)
    return files


def create_archive_for_latest_coding_job(
    *,
    user_id: int,
    conversation_id: str,
    request_id: str,
) -> Dict[str, Any]:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        raise VeliaCodeArchiveError("code_archive_request_id_missing", status=400)
    ensure_velia_code_archive_tables()
    existing = archive_metadata_for_request(normalized_request_id, int(user_id))
    if existing:
        return existing

    job = _latest_completed_job(int(user_id), str(conversation_id))
    if not job:
        raise VeliaCodeArchiveError("code_archive_completed_job_missing", status=404)
    project = project_service.get_project(int(user_id), str(job.get("project_id") or ""))
    branch = str(job.get("work_branch") or "")
    paths = _result_paths(job)
    files = _read_final_files(project, branch, paths)
    data = _zip_bytes(files)
    archive_id = str(uuid.uuid4())
    filename = _archive_filename(str(project.get("repository_full_name") or ""), str(job.get("job_id") or ""))
    digest = hashlib.sha256(data).hexdigest()

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_code_archives (
                archive_id, user_id, conversation_id, request_id, coding_job_id,
                repository_full_name, work_branch, filename, mime_type,
                archive_bytes, size_bytes, sha256, file_count
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'application/zip',%s,%s,%s,%s)
            ON CONFLICT (request_id) DO NOTHING
            """,
            (
                archive_id,
                int(user_id),
                str(conversation_id),
                normalized_request_id,
                str(job.get("job_id") or ""),
                str(project.get("repository_full_name") or ""),
                branch,
                filename,
                data,
                len(data),
                digest,
                len(files),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    metadata = archive_metadata_for_request(normalized_request_id, int(user_id))
    if not metadata:
        raise VeliaCodeArchiveError("code_archive_persist_failed", status=500)
    return metadata


def _signing_secret() -> bytes:
    configured = str(os.getenv("VELIA_CODE_ARCHIVE_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_IMAGES_SIGNING_SECRET", "") or "").strip()
    if not configured:
        configured = str(os.getenv("VELYON_IMAGES_API_KEY", "") or "").strip()
    if not configured:
        raise VeliaCodeArchiveError("code_archive_signing_secret_missing", status=503)
    return hashlib.sha256((configured + ":velia-code-archives").encode("utf-8")).digest()


def sign_archive_url(archive_id: str, user_id: int, expires_at: int) -> str:
    payload = f"{archive_id}:{int(user_id)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(_signing_secret(), payload, hashlib.sha256).hexdigest()


def verify_archive_signature(archive_id: str, user_id: int, expires_at: int, signature: str) -> bool:
    if int(expires_at or 0) < int(time.time()):
        return False
    try:
        expected = sign_archive_url(archive_id, int(user_id), int(expires_at))
    except VeliaCodeArchiveError:
        return False
    return hmac.compare_digest(expected, str(signature or ""))


def archive_metadata_for_request(request_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    ensure_velia_code_archive_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT archive_id, filename, mime_type, size_bytes, sha256, file_count,
                   repository_full_name, work_branch
            FROM velia_code_archives
            WHERE request_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(request_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        archive_id = str(_row_value(row, "archive_id", 0, "") or "")
        expires_at = int(time.time()) + _env_int(
            "VELIA_CODE_ARCHIVE_URL_TTL_SECONDS", 604800, 60, 604800
        )
        signature = sign_archive_url(archive_id, int(user_id), expires_at)
        return {
            "id": archive_id,
            "filename": str(_row_value(row, "filename", 1, "VELIA-code.zip") or "VELIA-code.zip"),
            "content_url": (
                f"/api/mobile/code-archives/{archive_id}/content?user_id={int(user_id)}"
                f"&expires={expires_at}&signature={signature}"
            ),
            "mime_type": str(_row_value(row, "mime_type", 2, "application/zip") or "application/zip"),
            "size_bytes": int(_row_value(row, "size_bytes", 3, 0) or 0),
            "sha256": str(_row_value(row, "sha256", 4, "") or ""),
            "file_count": int(_row_value(row, "file_count", 5, 0) or 0),
            "repository_full_name": str(_row_value(row, "repository_full_name", 6, "") or ""),
            "work_branch": str(_row_value(row, "work_branch", 7, "") or ""),
        }
    finally:
        cursor.close()
        conn.close()


def get_archive_content(archive_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    ensure_velia_code_archive_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT filename, mime_type, archive_bytes, size_bytes, sha256
            FROM velia_code_archives
            WHERE archive_id=%s AND user_id=%s
            LIMIT 1
            """,
            (str(archive_id), int(user_id)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        raw = _row_value(row, "archive_bytes", 2, b"")
        data = bytes(raw) if raw is not None else b""
        return {
            "filename": str(_row_value(row, "filename", 0, "VELIA-code.zip") or "VELIA-code.zip"),
            "mime_type": str(_row_value(row, "mime_type", 1, "application/zip") or "application/zip"),
            "bytes": data,
            "size_bytes": int(_row_value(row, "size_bytes", 3, len(data)) or len(data)),
            "sha256": str(_row_value(row, "sha256", 4, "") or ""),
        }
    finally:
        cursor.close()
        conn.close()
