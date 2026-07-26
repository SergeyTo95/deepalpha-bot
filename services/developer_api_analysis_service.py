import json
import logging
import os
import re
import signal
import socket
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from agents.chief_agent import ChiefAgent
from db.database import get_connection
from services.decision_first_renderer_patch import extract_decision_summary
from services.developer_api_billing_service import (
    ApiBillingError,
    complete_api_job_failure,
    complete_api_job_success,
    create_billed_api_job,
    ensure_api_billing_tables,
)
from services.developer_portal_service import ensure_developer_portal_tables
from services.webapp_report_formatter import build_webapp_analysis_report

logger = logging.getLogger(__name__)

QUICK_ANALYSIS_JOB_TYPE = "quick_analysis"
QUICK_ANALYSIS_PRODUCT_CODE = "quick_analysis"
PUBLIC_RESULT_SCHEMA_VERSION = "1.0"
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{32}$")
_PROVIDER_NAME_RE = re.compile(
    r"\b(?:kimi(?:[-_ ]?k?\d+)?|gemini(?:[-_ ]?[\w.]+)?|moonshot)\b",
    re.IGNORECASE,
)

_ANALYSIS_TABLES_READY = False


class ApiAnalysisError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = str(code)
        self.details = details


class AnalysisExecutionTimeout(TimeoutError):
    pass


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _safe_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def api_analysis_active_job_limit() -> int:
    return _safe_env_int("API_ANALYSIS_MAX_ACTIVE_JOBS_PER_CLIENT", 2, 1, 20)


def api_analysis_timeout_seconds() -> int:
    return _safe_env_int("API_ANALYSIS_TIMEOUT_SECONDS", 120, 30, 600)


def api_analysis_lease_seconds() -> int:
    configured = _safe_env_int("API_ANALYSIS_LEASE_SECONDS", 0, 0, 1800)
    return configured or min(1800, api_analysis_timeout_seconds() + 120)


def api_analysis_max_attempts() -> int:
    return _safe_env_int("API_ANALYSIS_MAX_ATTEMPTS", 2, 1, 5)


def api_analysis_poll_seconds() -> float:
    try:
        value = float(str(os.getenv("API_ANALYSIS_POLL_SECONDS", "2") or "2"))
    except Exception:
        value = 2.0
    return max(0.25, min(value, 30.0))


def normalize_language(value: Any) -> str:
    language = str(value or "en").strip().lower()
    if language.startswith("ru"):
        return "ru"
    if language.startswith("en"):
        return "en"
    raise ApiAnalysisError("invalid_language", allowed=["ru", "en"])


def normalize_polymarket_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 500:
        raise ApiAnalysisError("invalid_market_url")
    try:
        parsed = urlparse(raw)
    except Exception as exc:
        raise ApiAnalysisError("invalid_market_url") from exc
    host = str(parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or host not in {"polymarket.com", "www.polymarket.com"}:
        raise ApiAnalysisError("invalid_market_url")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ApiAnalysisError("invalid_market_url")
    path = str(parsed.path or "")
    if not (path.startswith("/event/") or path.startswith("/market/")):
        raise ApiAnalysisError("invalid_market_url")
    slug = path.split("/", 3)[2].strip() if len(path.split("/")) > 2 else ""
    if not slug or len(slug) > 240 or not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ApiAnalysisError("invalid_market_url")
    clean_path = path.rstrip("/")
    return f"https://polymarket.com{clean_path}"


def normalize_quick_analysis_request(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiAnalysisError("invalid_json")
    mode = str(payload.get("mode") or "quick").strip().lower()
    if mode != "quick":
        raise ApiAnalysisError("invalid_mode", allowed=["quick"])
    market_url = normalize_polymarket_url(payload.get("market_url") or payload.get("url"))
    language = normalize_language(payload.get("language") or payload.get("lang") or "en")
    return {
        "market_url": market_url,
        "mode": "quick",
        "language": language,
    }


def validate_job_id(value: Any) -> str:
    job_id = str(value or "").strip()
    if not _JOB_ID_RE.fullmatch(job_id):
        raise ApiAnalysisError("invalid_job_id")
    return job_id


def ensure_api_analysis_tables() -> None:
    global _ANALYSIS_TABLES_READY
    if _ANALYSIS_TABLES_READY:
        return
    ensure_api_billing_tables()
    ensure_developer_portal_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS worker_id TEXT")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS started_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_jobs ADD COLUMN IF NOT EXISTS lease_until TIMESTAMP")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_jobs_queue ON api_jobs(status, job_type, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_jobs_client_status ON api_jobs(client_id, status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_jobs_lease ON api_jobs(status, lease_until)"
        )
        conn.commit()
        _ANALYSIS_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _existing_idempotent_job(cursor, client_id: int, idempotency_key: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM api_credit_reservations WHERE client_id=%s AND idempotency_key=%s LIMIT 1",
        (int(client_id), str(idempotency_key)),
    )
    return cursor.fetchone() is not None


def submit_quick_analysis_job(
    *,
    client_id: int,
    key_id: int,
    idempotency_key: str,
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Serialize submissions per client, then reserve credits and create one durable job."""
    ensure_api_analysis_tables()
    cid = int(client_id)
    lock_conn = get_connection()
    lock_cursor = lock_conn.cursor()
    advisory_key = 9_100_000_000 + cid
    try:
        lock_cursor.execute("SELECT pg_advisory_lock(%s)", (advisory_key,))
        lock_conn.commit()
        is_repeat = _existing_idempotent_job(lock_cursor, cid, idempotency_key)
        if not is_repeat:
            lock_cursor.execute(
                """
                SELECT COUNT(*)
                FROM api_jobs
                WHERE client_id=%s AND status IN ('queued', 'running')
                """,
                (cid,),
            )
            row = lock_cursor.fetchone()
            active = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
            limit = api_analysis_active_job_limit()
            if active >= limit:
                raise ApiAnalysisError("active_job_limit_reached", limit=limit, active=active)
        return create_billed_api_job(
            client_id=cid,
            key_id=int(key_id),
            job_type=QUICK_ANALYSIS_JOB_TYPE,
            product_code=QUICK_ANALYSIS_PRODUCT_CODE,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    finally:
        try:
            lock_cursor.execute("SELECT pg_advisory_unlock(%s)", (advisory_key,))
            lock_conn.commit()
        except Exception:
            lock_conn.rollback()
        lock_cursor.close()
        lock_conn.close()


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None or value == "":
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_api_analysis_job(client_id: int, job_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_analysis_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT j.*,
                   r.status AS reservation_status,
                   r.units AS reservation_units,
                   r.reservation_id
            FROM api_jobs j
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE j.client_id=%s AND j.job_id=%s AND j.job_type=%s
            LIMIT 1
            """,
            (int(client_id), str(job_id), QUICK_ANALYSIS_JOB_TYPE),
        )
        return _row_to_dict(cursor, cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def serialize_api_analysis_job(job: Dict[str, Any]) -> Dict[str, Any]:
    status = str(job.get("status") or "queued")
    request_payload = _parse_json_object(job.get("request_json"))
    reservation_status = str(job.get("reservation_status") or "")
    reserved = int(job.get("units_reserved") or job.get("reservation_units") or 0)
    charged = int(job.get("units_charged") or 0)
    refunded = reserved if reservation_status == "refunded" else 0
    progress = int(job.get("progress") or 0)
    if status in {"success", "error"}:
        progress = 100
    payload: Dict[str, Any] = {
        "ok": True,
        "job_id": str(job.get("job_id") or ""),
        "status": status,
        "analysis_type": "quick",
        "mode": "quick",
        "market_url": str(request_payload.get("market_url") or ""),
        "language": str(request_payload.get("language") or "en"),
        "progress": max(0, min(progress, 100)),
        "credits": {
            "reserved": reserved,
            "charged": charged,
            "refunded": refunded,
            "reservation_status": reservation_status or None,
        },
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }
    if status == "success":
        payload["result"] = _parse_json_object(job.get("result_json"))
    elif status == "error":
        payload["error"] = str(job.get("error") or "analysis_failed")
    return payload


def _sanitize_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    text = _PROVIDER_NAME_RE.sub("AI model", text)
    return text[:limit]


def _number(value: Any, *, minimum: Optional[float] = None, maximum: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return round(number, 4)


def _string_list(candidates: Iterable[Any], limit: int = 8, item_limit: int = 600) -> List[str]:
    output: List[str] = []
    for item in candidates or []:
        if isinstance(item, dict):
            value = item.get("title") or item.get("reason") or item.get("text") or item.get("signal")
        else:
            value = item
        clean = _sanitize_text(value, item_limit)
        if clean and clean not in output:
            output.append(clean)
        if len(output) >= limit:
            break
    return output


def _valid_public_url(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return raw[:1200]


def _public_sources(raw_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = raw_result.get("relevant_sources") or raw_result.get("news_sources") or []
    if not isinstance(candidates, list):
        return []
    output: List[Dict[str, Any]] = []
    seen = set()
    for item in candidates:
        if isinstance(item, str):
            url = _valid_public_url(item)
            title = ""
            source = ""
            published_at = ""
        elif isinstance(item, dict):
            url = _valid_public_url(item.get("url") or item.get("link") or item.get("source_url"))
            title = _sanitize_text(item.get("title") or item.get("name") or item.get("headline"), 300)
            source = _sanitize_text(item.get("source") or item.get("domain") or item.get("publisher"), 120)
            published_at = _sanitize_text(item.get("published_at") or item.get("date") or item.get("published"), 80)
        else:
            continue
        dedupe = url or f"{source}:{title}"
        if not dedupe or dedupe in seen:
            continue
        seen.add(dedupe)
        output.append({
            "title": title,
            "url": url,
            "source": source,
            "published_at": published_at,
        })
        if len(output) >= 12:
            break
    return output


def _extract_nested_list(raw_result: Dict[str, Any], keys: Iterable[str]) -> List[Any]:
    containers = [
        raw_result,
        raw_result.get("forecast_card") if isinstance(raw_result.get("forecast_card"), dict) else {},
        raw_result.get("trading_plan") if isinstance(raw_result.get("trading_plan"), dict) else {},
        raw_result.get("decision_data") if isinstance(raw_result.get("decision_data"), dict) else {},
    ]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if isinstance(value, list) and value:
                return value
    return []


def build_public_quick_analysis_result(
    raw_result: Dict[str, Any],
    *,
    market_url: str,
    language: str,
) -> Dict[str, Any]:
    if not isinstance(raw_result, dict) or not raw_result:
        raise ApiAnalysisError("invalid_analysis_result")
    report = build_webapp_analysis_report(raw_result=raw_result, market_url=market_url, lang=language)
    decision = extract_decision_summary(raw_result)
    verdict = str(decision.get("verdict") or "NO_TRADE").upper().strip()
    if verdict not in {"BUY", "WATCH", "WAIT", "NO_TRADE"}:
        verdict = "NO_TRADE"
    side = str(decision.get("side") or "").upper().strip()
    if side not in {"YES", "NO"}:
        side = None

    factors = _string_list(
        _extract_nested_list(raw_result, ("key_factors", "factors", "key_signals", "drivers")),
        limit=8,
    )
    risks = _string_list(
        _extract_nested_list(raw_result, ("risks", "risk_flags", "limitations")),
        limit=8,
    )
    summary = _sanitize_text(
        report.get("conclusion")
        or decision.get("reason")
        or raw_result.get("conclusion")
        or raw_result.get("reasoning"),
        2400,
    )
    reasoning = _sanitize_text(raw_result.get("reasoning") or raw_result.get("full_analysis"), 7000)
    analysis_text = _sanitize_text(report.get("canonical_text") or report.get("copy_text"), 14000)
    generated_at = datetime.now(timezone.utc).isoformat()

    independent = decision.get("independent_probability")
    if independent is None:
        forecast_card = raw_result.get("forecast_card") if isinstance(raw_result.get("forecast_card"), dict) else {}
        independent = forecast_card.get("independent_probability")

    return {
        "schema_version": PUBLIC_RESULT_SCHEMA_VERSION,
        "analysis_type": "quick",
        "question": _sanitize_text(report.get("question") or raw_result.get("question"), 500),
        "market_url": market_url,
        "market_slug": _sanitize_text(report.get("market_slug"), 240),
        "decision": verdict,
        "side": side,
        "fair_probability": _number(decision.get("fair_probability"), minimum=0, maximum=100),
        "market_probability": _number(decision.get("market_probability"), minimum=0, maximum=100),
        "edge_pp": _number(decision.get("edge_pp"), minimum=-100, maximum=100),
        "confidence": _sanitize_text(decision.get("confidence") or report.get("confidence"), 40).lower(),
        "data_quality_score": _number(decision.get("data_quality_score"), minimum=0, maximum=10),
        "independent_probability": bool(independent) if independent is not None else None,
        "summary": summary,
        "reasoning": reasoning,
        "factors": factors,
        "risks": risks,
        "sources": _public_sources(raw_result),
        "analysis_text": analysis_text,
        "generated_at": generated_at,
    }


def _owner_user_id(client_id: int) -> int:
    ensure_developer_portal_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT user_id FROM api_client_owners WHERE client_id=%s LIMIT 1",
            (int(client_id),),
        )
        row = cursor.fetchone()
        if not row:
            return 0
        value = row.get("user_id") if isinstance(row, dict) else row[0]
        return max(0, int(value or 0))
    except Exception:
        return 0
    finally:
        cursor.close()
        conn.close()


@contextmanager
def _execution_timeout(seconds: int):
    if not hasattr(signal, "SIGALRM"):
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)

    def timeout_handler(_signum, _frame):
        raise AnalysisExecutionTimeout("analysis_timeout")

    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, max(1, int(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def execute_quick_analysis_job(job: Dict[str, Any]) -> Dict[str, Any]:
    request_payload = _parse_json_object(job.get("request_json"))
    normalized = normalize_quick_analysis_request(request_payload)
    owner_user_id = _owner_user_id(int(job.get("client_id") or 0))
    with _execution_timeout(api_analysis_timeout_seconds()):
        raw_result = ChiefAgent().run(
            normalized["market_url"],
            lang=normalized["language"],
            user_id=owner_user_id,
            persist=False,
        )
    return build_public_quick_analysis_result(
        raw_result,
        market_url=normalized["market_url"],
        language=normalized["language"],
    )


def claim_next_quick_analysis_job(worker_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_analysis_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT job_id
            FROM api_jobs
            WHERE status='queued' AND job_type=%s
            ORDER BY created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (QUICK_ANALYSIS_JOB_TYPE,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        job_id = row.get("job_id") if isinstance(row, dict) else row[0]
        cursor.execute(
            """
            UPDATE api_jobs
            SET status='running', progress=10, worker_id=%s,
                attempt_count=attempt_count+1,
                started_at=COALESCE(started_at, NOW()),
                heartbeat_at=NOW(),
                lease_until=NOW() + make_interval(secs => %s),
                error=NULL,
                updated_at=NOW()
            WHERE job_id=%s AND status='queued'
            RETURNING *
            """,
            (str(worker_id)[:120], api_analysis_lease_seconds(), str(job_id)),
        )
        job = _row_to_dict(cursor, cursor.fetchone())
        conn.commit()
        return job
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def update_api_analysis_progress(job_id: str, worker_id: str, progress: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_jobs
            SET progress=%s, heartbeat_at=NOW(),
                lease_until=NOW() + make_interval(secs => %s),
                updated_at=NOW()
            WHERE job_id=%s AND status='running' AND worker_id=%s
            """,
            (
                max(0, min(int(progress), 99)),
                api_analysis_lease_seconds(),
                str(job_id),
                str(worker_id)[:120],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def recover_stale_api_analysis_jobs() -> Dict[str, int]:
    ensure_api_analysis_tables()
    max_attempts = api_analysis_max_attempts()
    retry_count = 0
    refund_ids: List[str] = []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT job_id, status, attempt_count
            FROM api_jobs
            WHERE job_type=%s AND (
                status='refund_pending'
                OR (status='running' AND (lease_until IS NULL OR lease_until < NOW()))
            )
            ORDER BY updated_at ASC
            FOR UPDATE SKIP LOCKED
            """,
            (QUICK_ANALYSIS_JOB_TYPE,),
        )
        rows = cursor.fetchall() or []
        for raw in rows:
            item = _row_to_dict(cursor, raw) or {}
            job_id = str(item.get("job_id") or "")
            status = str(item.get("status") or "")
            attempts = int(item.get("attempt_count") or 0)
            if status == "running" and attempts < max_attempts:
                cursor.execute(
                    """
                    UPDATE api_jobs
                    SET status='queued', progress=0, worker_id=NULL,
                        heartbeat_at=NULL, lease_until=NULL,
                        error='worker_retry', updated_at=NOW()
                    WHERE job_id=%s
                    """,
                    (job_id,),
                )
                retry_count += 1
            else:
                cursor.execute(
                    """
                    UPDATE api_jobs
                    SET status='refund_pending', worker_id=NULL,
                        heartbeat_at=NULL, lease_until=NULL, updated_at=NOW()
                    WHERE job_id=%s
                    """,
                    (job_id,),
                )
                refund_ids.append(job_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    refunded = 0
    for job_id in refund_ids:
        try:
            complete_api_job_failure(job_id, "analysis_worker_unavailable")
            refunded += 1
        except Exception:
            logger.exception("API_ANALYSIS_STALE_REFUND_FAILED job_id=%s", job_id)
    return {"retried": retry_count, "refunded": refunded}


def process_claimed_quick_analysis_job(job: Dict[str, Any], worker_id: str) -> None:
    job_id = str(job.get("job_id") or "")
    try:
        update_api_analysis_progress(job_id, worker_id, 20)
        public_result = execute_quick_analysis_job(job)
        update_api_analysis_progress(job_id, worker_id, 90)
        complete_api_job_success(job_id, public_result)
        _mark_job_finished(job_id, progress=100)
        logger.info("API_ANALYSIS_JOB_SUCCESS job_id=%s client_id=%s", job_id, job.get("client_id"))
    except AnalysisExecutionTimeout:
        logger.warning("API_ANALYSIS_JOB_TIMEOUT job_id=%s", job_id)
        complete_api_job_failure(job_id, "analysis_timeout")
        _mark_job_finished(job_id, progress=100)
    except ApiAnalysisError as exc:
        logger.warning("API_ANALYSIS_JOB_INVALID job_id=%s error=%s", job_id, exc.code)
        complete_api_job_failure(job_id, exc.code)
        _mark_job_finished(job_id, progress=100)
    except Exception:
        logger.exception("API_ANALYSIS_JOB_FAILED job_id=%s", job_id)
        complete_api_job_failure(job_id, "analysis_failed")
        _mark_job_finished(job_id, progress=100)


def _mark_job_finished(job_id: str, progress: int = 100) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_jobs
            SET progress=%s, finished_at=COALESCE(finished_at, NOW()),
                worker_id=NULL, heartbeat_at=NULL, lease_until=NULL,
                updated_at=NOW()
            WHERE job_id=%s AND status IN ('success', 'error')
            """,
            (max(0, min(int(progress), 100)), str(job_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("API_ANALYSIS_MARK_FINISHED_FAILED job_id=%s", job_id)
    finally:
        cursor.close()
        conn.close()


def run_api_analysis_worker_forever(worker_id: Optional[str] = None) -> None:
    ensure_api_analysis_tables()
    identity = str(worker_id or f"{socket.gethostname()}:{os.getpid()}")[:120]
    logger.info(
        "API_ANALYSIS_WORKER_STARTED worker_id=%s timeout=%s lease=%s max_attempts=%s",
        identity,
        api_analysis_timeout_seconds(),
        api_analysis_lease_seconds(),
        api_analysis_max_attempts(),
    )
    last_recovery = 0.0
    while True:
        now = time.monotonic()
        if now - last_recovery >= 30:
            try:
                recovered = recover_stale_api_analysis_jobs()
                if recovered["retried"] or recovered["refunded"]:
                    logger.warning("API_ANALYSIS_STALE_RECOVERY %s", recovered)
            except Exception:
                logger.exception("API_ANALYSIS_STALE_RECOVERY_FAILED")
            last_recovery = now
        try:
            job = claim_next_quick_analysis_job(identity)
        except Exception:
            logger.exception("API_ANALYSIS_JOB_CLAIM_FAILED")
            time.sleep(max(1.0, api_analysis_poll_seconds()))
            continue
        if not job:
            time.sleep(api_analysis_poll_seconds())
            continue
        process_claimed_quick_analysis_job(job, identity)
