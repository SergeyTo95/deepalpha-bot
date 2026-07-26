import json
import logging
import os
import signal
import socket
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence

from db.database import get_connection
from services.developer_api_analysis_service import ensure_api_analysis_tables
from services.developer_api_billing_service import (
    complete_api_job_failure,
    complete_api_job_success,
    create_billed_api_job,
)
from services.developer_api_observability_service import ensure_api_observability_tables
from services.free_opportunity_scanner import scan_free_opportunities

logger = logging.getLogger(__name__)

OPPORTUNITY_SCAN_JOB_TYPE = "opportunity_scan"
OPPORTUNITY_SCAN_PRODUCT_CODE = "opportunity_scan"
OPPORTUNITY_SCAN_SCHEMA_VERSION = "1.0"

ALLOWED_CATEGORIES = {
    "all": "All",
    "crypto": "Crypto",
    "politics": "Politics",
    "sports": "Sports",
    "economy": "Economy",
    "tech": "Tech",
    "other": "Other",
}
ALLOWED_TIERS = {
    "DEEP_ANALYSIS_CANDIDATE",
    "WATCH_CANDIDATE",
    "LOW_PRIORITY",
}

_REASON_TRANSLATIONS = {
    "достаточная ликвидность": "sufficient liquidity",
    "активный объём за 24 часа": "active 24-hour volume",
    "накопленный интерес рынка": "strong accumulated market interest",
    "цена ещё не выглядит полностью решённой": "price still allows meaningful discovery",
    "подходящее расстояние до дедлайна": "useful time remaining before resolution",
    "заметное движение цены": "meaningful recent price movement",
    "несколько контрактов внутри события": "multiple contracts within the event",
    "вопрос допускает проверку объективными данными": "question can be checked with objective data",
    "можно сравнить соседние контракты события": "related event contracts can be compared",
}


class ApiOpportunityError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = str(code)
        self.details = details


class OpportunityExecutionTimeout(TimeoutError):
    pass


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _rows_to_dicts(cursor, rows) -> List[Dict[str, Any]]:
    return [item for item in (_row_to_dict(cursor, row) for row in rows or []) if item]


def _safe_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def opportunity_active_job_limit() -> int:
    return _safe_env_int("API_OPPORTUNITY_MAX_ACTIVE_JOBS_PER_CLIENT", 2, 1, 20)


def opportunity_timeout_seconds() -> int:
    return _safe_env_int("API_OPPORTUNITY_TIMEOUT_SECONDS", 45, 10, 300)


def opportunity_lease_seconds() -> int:
    return _safe_env_int(
        "API_OPPORTUNITY_LEASE_SECONDS",
        opportunity_timeout_seconds() + 60,
        30,
        900,
    )


def opportunity_max_attempts() -> int:
    return _safe_env_int("API_OPPORTUNITY_MAX_ATTEMPTS", 2, 1, 5)


def opportunity_worker_stale_seconds() -> int:
    return _safe_env_int(
        "API_OPPORTUNITY_WORKER_STALE_SECONDS",
        max(90, opportunity_timeout_seconds() + 30),
        20,
        900,
    )


def opportunity_poll_seconds() -> float:
    try:
        value = float(str(os.getenv("API_OPPORTUNITY_POLL_SECONDS", "1") or "1"))
    except Exception:
        value = 1.0
    return max(0.2, min(value, 30.0))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, code: str) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ApiOpportunityError(code, minimum=minimum, maximum=maximum) from exc
    if parsed < minimum or parsed > maximum:
        raise ApiOpportunityError(code, minimum=minimum, maximum=maximum)
    return parsed


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float, code: str) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ApiOpportunityError(code, minimum=minimum, maximum=maximum) from exc
    if parsed != parsed or parsed < minimum or parsed > maximum:
        raise ApiOpportunityError(code, minimum=minimum, maximum=maximum)
    return round(parsed, 4)


def normalize_opportunity_language(value: Any) -> str:
    language = str(value or "en").strip().lower()
    if language.startswith("ru"):
        return "ru"
    if language.startswith("en"):
        return "en"
    raise ApiOpportunityError("invalid_language", allowed=["ru", "en"])


def normalize_opportunity_category(value: Any) -> str:
    category = str(value or "All").strip().lower()
    if category not in ALLOWED_CATEGORIES:
        raise ApiOpportunityError(
            "invalid_category",
            allowed=list(ALLOWED_CATEGORIES.values()),
        )
    return ALLOWED_CATEGORIES[category]


def normalize_opportunity_tiers(value: Any) -> List[str]:
    if value in (None, ""):
        return sorted(ALLOWED_TIERS)
    if not isinstance(value, list):
        raise ApiOpportunityError("invalid_tiers", allowed=sorted(ALLOWED_TIERS))
    result: List[str] = []
    unknown: List[str] = []
    for raw in value:
        tier = str(raw or "").strip().upper()
        if tier not in ALLOWED_TIERS:
            unknown.append(tier)
        elif tier not in result:
            result.append(tier)
    if unknown or not result:
        raise ApiOpportunityError(
            "invalid_tiers",
            allowed=sorted(ALLOWED_TIERS),
            unknown=unknown,
        )
    return result


def normalize_opportunity_scan_request(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiOpportunityError("invalid_json")
    unsupported = sorted(
        key for key in payload
        if key not in {
            "category",
            "language",
            "scan_limit",
            "result_limit",
            "min_score",
            "min_liquidity",
            "min_volume_24h",
            "tiers",
        }
    )
    if unsupported:
        raise ApiOpportunityError("unsupported_fields", fields=unsupported)
    return {
        "category": normalize_opportunity_category(payload.get("category")),
        "language": normalize_opportunity_language(payload.get("language") or "en"),
        "scan_limit": _bounded_int(
            payload.get("scan_limit"),
            default=100,
            minimum=10,
            maximum=200,
            code="invalid_scan_limit",
        ),
        "result_limit": _bounded_int(
            payload.get("result_limit"),
            default=10,
            minimum=1,
            maximum=20,
            code="invalid_result_limit",
        ),
        "min_score": _bounded_int(
            payload.get("min_score"),
            default=0,
            minimum=0,
            maximum=100,
            code="invalid_min_score",
        ),
        "min_liquidity": _bounded_float(
            payload.get("min_liquidity"),
            default=0.0,
            minimum=0.0,
            maximum=1_000_000_000.0,
            code="invalid_min_liquidity",
        ),
        "min_volume_24h": _bounded_float(
            payload.get("min_volume_24h"),
            default=0.0,
            minimum=0.0,
            maximum=1_000_000_000.0,
            code="invalid_min_volume_24h",
        ),
        "tiers": normalize_opportunity_tiers(payload.get("tiers")),
    }


def ensure_api_opportunity_tables() -> None:
    ensure_api_analysis_tables()
    ensure_api_observability_tables()


def _existing_idempotent_job(cursor, client_id: int, idempotency_key: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM api_credit_reservations WHERE client_id=%s AND idempotency_key=%s LIMIT 1",
        (int(client_id), str(idempotency_key)),
    )
    return cursor.fetchone() is not None


def submit_opportunity_scan_job(
    *,
    client_id: int,
    key_id: int,
    idempotency_key: str,
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_api_opportunity_tables()
    cid = int(client_id)
    conn = get_connection()
    cursor = conn.cursor()
    advisory_key = 9_100_000_000 + cid
    try:
        cursor.execute("SELECT pg_advisory_lock(%s)", (advisory_key,))
        conn.commit()
        repeated = _existing_idempotent_job(cursor, cid, idempotency_key)
        if not repeated:
            cursor.execute(
                "SELECT COUNT(*) FROM api_jobs WHERE client_id=%s AND status IN ('queued','running')",
                (cid,),
            )
            row = cursor.fetchone()
            active = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
            limit = opportunity_active_job_limit()
            if active >= limit:
                raise ApiOpportunityError("active_job_limit_reached", limit=limit, active=active)
        return create_billed_api_job(
            client_id=cid,
            key_id=int(key_id),
            job_type=OPPORTUNITY_SCAN_JOB_TYPE,
            product_code=OPPORTUNITY_SCAN_PRODUCT_CODE,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    finally:
        try:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (advisory_key,))
            conn.commit()
        except Exception:
            conn.rollback()
        cursor.close()
        conn.close()


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def get_opportunity_scan_job(client_id: int, job_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_opportunity_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT j.*, r.status AS reservation_status,
                   r.units AS reservation_units, r.reservation_id
            FROM api_jobs j
            LEFT JOIN api_credit_reservations r ON r.job_id=j.job_id
            WHERE j.client_id=%s AND j.job_id=%s AND j.job_type=%s
            LIMIT 1
            """,
            (int(client_id), str(job_id), OPPORTUNITY_SCAN_JOB_TYPE),
        )
        return _row_to_dict(cursor, cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def serialize_opportunity_scan_job(job: Dict[str, Any]) -> Dict[str, Any]:
    status = str(job.get("status") or "queued")
    request_payload = _parse_json_object(job.get("request_json"))
    reservation_status = str(job.get("reservation_status") or "")
    reserved = int(job.get("units_reserved") or job.get("reservation_units") or 0)
    charged = int(job.get("units_charged") or 0)
    progress = int(job.get("progress") or 0)
    if status in {"success", "error"}:
        progress = 100
    payload: Dict[str, Any] = {
        "ok": True,
        "job_id": str(job.get("job_id") or ""),
        "status": status,
        "job_type": OPPORTUNITY_SCAN_JOB_TYPE,
        "progress": max(0, min(progress, 100)),
        "request": request_payload,
        "credits": {
            "reserved": reserved,
            "charged": charged,
            "refunded": reserved if reservation_status == "refunded" else 0,
            "reservation_status": reservation_status or None,
        },
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }
    if status == "success":
        payload["result"] = _parse_json_object(job.get("result_json"))
    elif status == "error":
        payload["error"] = str(job.get("error") or "opportunity_scan_failed")
    return payload


def _translate_reason(value: Any, language: str) -> str:
    text = str(value or "").strip()
    if language == "ru":
        return text[:300]
    if text in _REASON_TRANSLATIONS:
        return _REASON_TRANSLATIONS[text]
    if text.startswith("движение линии за 24ч около"):
        suffix = text.split("около", 1)[-1].strip()
        return f"24-hour price movement around {suffix}"
    return text[:300]


def _public_candidate(candidate: Dict[str, Any], language: str) -> Dict[str, Any]:
    reasons = candidate.get("reasons") if isinstance(candidate.get("reasons"), list) else []
    risks = candidate.get("risk_flags") if isinstance(candidate.get("risk_flags"), list) else []
    components = candidate.get("score_components") if isinstance(candidate.get("score_components"), dict) else {}
    return {
        "market_id": str(candidate.get("market_id") or "")[:200],
        "event_key": str(candidate.get("event_key") or "")[:240],
        "question": str(candidate.get("question") or "")[:500],
        "url": str(candidate.get("url") or "")[:1200],
        "category": str(candidate.get("category") or "Other")[:80],
        "yes_price": round(float(candidate.get("yes_price") or 0), 2),
        "no_price": round(float(candidate.get("no_price") or 0), 2),
        "liquidity": round(float(candidate.get("liquidity") or 0), 2),
        "volume_24h": round(float(candidate.get("volume_24h") or 0), 2),
        "volume_total": round(float(candidate.get("volume_total") or 0), 2),
        "hours_to_close": (
            round(float(candidate.get("hours_to_close")), 2)
            if candidate.get("hours_to_close") is not None
            else None
        ),
        "price_move_24h_pp": round(float(candidate.get("price_move_24h_pp") or 0), 2),
        "event_market_count": int(candidate.get("event_market_count") or 0),
        "score": max(0, min(int(candidate.get("score") or 0), 100)),
        "tier": str(candidate.get("tier") or "LOW_PRIORITY"),
        "reasons": [_translate_reason(item, language) for item in reasons[:5] if str(item or "").strip()],
        "risk_flags": [str(item)[:120] for item in risks[:10] if str(item or "").strip()],
        "score_components": {
            str(key)[:80]: max(0, min(int(value or 0), 100))
            for key, value in list(components.items())[:20]
        },
    }


def execute_opportunity_scan(request_payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_opportunity_scan_request(request_payload)
    raw = scan_free_opportunities(
        scan_limit=normalized["scan_limit"],
        result_limit=25,
        category_filter=normalized["category"],
        force_refresh=False,
    )
    raw_candidates = raw.get("candidates") if isinstance(raw.get("candidates"), list) else []
    selected: List[Dict[str, Any]] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        if int(candidate.get("score") or 0) < normalized["min_score"]:
            continue
        if float(candidate.get("liquidity") or 0) < normalized["min_liquidity"]:
            continue
        if float(candidate.get("volume_24h") or 0) < normalized["min_volume_24h"]:
            continue
        if str(candidate.get("tier") or "") not in set(normalized["tiers"]):
            continue
        selected.append(_public_candidate(candidate, normalized["language"]))
        if len(selected) >= normalized["result_limit"]:
            break

    disclaimer = (
        "Детерминированный скрининг публичных данных. Это приоритет для дальнейшего анализа, а не BUY-сигнал. "
        "Справедливая вероятность и edge не рассчитываются."
        if normalized["language"] == "ru"
        else
        "Deterministic public-data screening. This ranks markets for further analysis and is not a BUY signal. "
        "Fair probability and edge are not calculated."
    )
    return {
        "schema_version": OPPORTUNITY_SCAN_SCHEMA_VERSION,
        "scan_type": OPPORTUNITY_SCAN_JOB_TYPE,
        "provider_calls": 0,
        "paid_ai_used": False,
        "category": normalized["category"],
        "language": normalized["language"],
        "filters": {
            "scan_limit": normalized["scan_limit"],
            "result_limit": normalized["result_limit"],
            "min_score": normalized["min_score"],
            "min_liquidity": normalized["min_liquidity"],
            "min_volume_24h": normalized["min_volume_24h"],
            "tiers": normalized["tiers"],
        },
        "markets_received": int(raw.get("markets_received") or 0),
        "eligible_markets": int(raw.get("eligible_markets") or 0),
        "candidate_count": len(selected),
        "candidates": selected,
        "rejection_counts": (
            raw.get("rejection_counts")
            if isinstance(raw.get("rejection_counts"), dict)
            else {}
        ),
        "source_cached": bool(raw.get("cached")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": disclaimer,
    }


@contextmanager
def _execution_timeout(seconds: int):
    if not hasattr(signal, "SIGALRM"):
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)

    def handler(_signum, _frame):
        raise OpportunityExecutionTimeout("opportunity_scan_timeout")

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, max(1, int(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def claim_next_opportunity_scan_job(worker_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_opportunity_tables()
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
            (OPPORTUNITY_SCAN_JOB_TYPE,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        job_id = row.get("job_id") if isinstance(row, dict) else row[0]
        cursor.execute(
            """
            UPDATE api_jobs
            SET status='running', progress=15, worker_id=%s,
                attempt_count=attempt_count+1,
                started_at=COALESCE(started_at, NOW()), heartbeat_at=NOW(),
                lease_until=NOW() + make_interval(secs => %s),
                error=NULL, updated_at=NOW()
            WHERE job_id=%s AND status='queued'
            RETURNING *
            """,
            (str(worker_id)[:120], opportunity_lease_seconds(), str(job_id)),
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


def _update_progress(job_id: str, worker_id: str, progress: int) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_jobs
            SET progress=%s, heartbeat_at=NOW(),
                lease_until=NOW() + make_interval(secs => %s), updated_at=NOW()
            WHERE job_id=%s AND status='running' AND worker_id=%s
            """,
            (
                max(0, min(int(progress), 99)),
                opportunity_lease_seconds(),
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


def _mark_finished(job_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_jobs
            SET progress=100, finished_at=COALESCE(finished_at, NOW()),
                worker_id=NULL, heartbeat_at=NULL, lease_until=NULL, updated_at=NOW()
            WHERE job_id=%s AND status IN ('success','error')
            """,
            (str(job_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("OPPORTUNITY_SCAN_MARK_FINISHED_FAILED job_id=%s", job_id)
    finally:
        cursor.close()
        conn.close()


def process_claimed_opportunity_scan_job(job: Dict[str, Any], worker_id: str) -> None:
    job_id = str(job.get("job_id") or "")
    try:
        request_payload = _parse_json_object(job.get("request_json"))
        _update_progress(job_id, worker_id, 30)
        with _execution_timeout(opportunity_timeout_seconds()):
            result = execute_opportunity_scan(request_payload)
        _update_progress(job_id, worker_id, 90)
        complete_api_job_success(job_id, result)
        _mark_finished(job_id)
        logger.info(
            "OPPORTUNITY_SCAN_JOB_SUCCESS job_id=%s candidates=%s cached=%s",
            job_id,
            result.get("candidate_count"),
            result.get("source_cached"),
        )
    except OpportunityExecutionTimeout:
        complete_api_job_failure(job_id, "opportunity_scan_timeout")
        _mark_finished(job_id)
    except ApiOpportunityError as exc:
        complete_api_job_failure(job_id, exc.code)
        _mark_finished(job_id)
    except Exception:
        logger.exception("OPPORTUNITY_SCAN_JOB_FAILED job_id=%s", job_id)
        complete_api_job_failure(job_id, "opportunity_scan_failed")
        _mark_finished(job_id)


def recover_stale_opportunity_scan_jobs() -> Dict[str, int]:
    ensure_api_opportunity_tables()
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
            (OPPORTUNITY_SCAN_JOB_TYPE,),
        )
        for row in cursor.fetchall() or []:
            item = _row_to_dict(cursor, row) or {}
            job_id = str(item.get("job_id") or "")
            status = str(item.get("status") or "")
            attempts = int(item.get("attempt_count") or 0)
            if status == "running" and attempts < opportunity_max_attempts():
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
            complete_api_job_failure(job_id, "opportunity_worker_unavailable")
            _mark_finished(job_id)
            refunded += 1
        except Exception:
            logger.exception("OPPORTUNITY_SCAN_STALE_REFUND_FAILED job_id=%s", job_id)
    return {"retried": retry_count, "refunded": refunded}


def touch_opportunity_worker(worker_id: str, status: str, job_id: Optional[str] = None) -> None:
    ensure_api_opportunity_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        metadata = json.dumps(
            {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "timeout_seconds": opportunity_timeout_seconds(),
                "max_attempts": opportunity_max_attempts(),
            },
            ensure_ascii=False,
        )
        cursor.execute(
            """
            INSERT INTO api_worker_heartbeats (
                worker_id, worker_type, status, current_job_id,
                started_at, last_seen_at, metadata_json
            ) VALUES (%s, 'opportunity_scan', %s, %s, NOW(), NOW(), %s)
            ON CONFLICT (worker_id) DO UPDATE SET
                worker_type='opportunity_scan', status=EXCLUDED.status,
                current_job_id=EXCLUDED.current_job_id,
                last_seen_at=NOW(), metadata_json=EXCLUDED.metadata_json
            """,
            (str(worker_id)[:120], str(status)[:40], str(job_id or "")[:80] or None, metadata),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_opportunity_runtime_health(*, include_workers: bool = False) -> Dict[str, Any]:
    ensure_api_opportunity_tables()
    stale_after = opportunity_worker_stale_seconds()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='queued') AS queued,
                COUNT(*) FILTER (WHERE status='running') AS running,
                COUNT(*) FILTER (WHERE status='refund_pending') AS refund_pending,
                COUNT(*) FILTER (WHERE status='running' AND (lease_until IS NULL OR lease_until < NOW())) AS stale_running,
                COUNT(*) FILTER (WHERE status='success' AND finished_at >= NOW() - INTERVAL '24 hours') AS success_24h,
                COUNT(*) FILTER (WHERE status='error' AND finished_at >= NOW() - INTERVAL '24 hours') AS error_24h,
                EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status='queued'))) AS oldest_queued_age_seconds,
                AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) FILTER (
                    WHERE status IN ('success','error') AND started_at IS NOT NULL
                      AND finished_at >= NOW() - INTERVAL '24 hours'
                ) AS avg_duration_seconds_24h
            FROM api_jobs
            WHERE job_type=%s
            """,
            (OPPORTUNITY_SCAN_JOB_TYPE,),
        )
        metrics = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            SELECT worker_id, status, current_job_id,
                   started_at, last_seen_at,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS heartbeat_age_seconds,
                   (last_seen_at >= NOW() - make_interval(secs => %s)) AS fresh
            FROM api_worker_heartbeats
            WHERE worker_type='opportunity_scan'
            ORDER BY last_seen_at DESC LIMIT 20
            """,
            (stale_after,),
        )
        workers = _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()

    fresh_workers = sum(1 for item in workers if bool(item.get("fresh")))
    queue = {
        "queued": int(metrics.get("queued") or 0),
        "running": int(metrics.get("running") or 0),
        "refund_pending": int(metrics.get("refund_pending") or 0),
        "stale_running": int(metrics.get("stale_running") or 0),
        "oldest_queued_age_seconds": round(float(metrics.get("oldest_queued_age_seconds") or 0), 1),
    }
    recent = {
        "success_24h": int(metrics.get("success_24h") or 0),
        "error_24h": int(metrics.get("error_24h") or 0),
        "avg_duration_seconds_24h": round(float(metrics.get("avg_duration_seconds_24h") or 0), 1),
    }
    warnings: List[str] = []
    if fresh_workers == 0:
        warnings.append("no_fresh_opportunity_worker")
    if queue["stale_running"]:
        warnings.append("stale_opportunity_jobs")
    if queue["refund_pending"]:
        warnings.append("opportunity_refunds_pending")
    result: Dict[str, Any] = {
        "status": "operational" if not warnings else "degraded",
        "worker_available": fresh_workers > 0,
        "fresh_workers": fresh_workers,
        "worker_stale_after_seconds": stale_after,
        "queue": queue,
        "recent": recent,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_workers:
        result["workers"] = workers
    return result


def run_opportunity_scan_worker_forever(worker_id: Optional[str] = None) -> None:
    ensure_api_opportunity_tables()
    identity = str(worker_id or f"opportunity:{socket.gethostname()}:{os.getpid()}")[:120]
    logger.info("OPPORTUNITY_SCAN_WORKER_STARTED worker_id=%s", identity)
    last_recovery = 0.0
    last_heartbeat = 0.0
    try:
        while True:
            now = time.monotonic()
            if now - last_heartbeat >= 10:
                try:
                    touch_opportunity_worker(identity, "idle")
                except Exception:
                    logger.exception("OPPORTUNITY_WORKER_HEARTBEAT_FAILED")
                last_heartbeat = now
            if now - last_recovery >= 30:
                try:
                    recovered = recover_stale_opportunity_scan_jobs()
                    if recovered["retried"] or recovered["refunded"]:
                        logger.warning("OPPORTUNITY_SCAN_STALE_RECOVERY %s", recovered)
                except Exception:
                    logger.exception("OPPORTUNITY_SCAN_STALE_RECOVERY_FAILED")
                last_recovery = now
            try:
                job = claim_next_opportunity_scan_job(identity)
            except Exception:
                logger.exception("OPPORTUNITY_SCAN_CLAIM_FAILED")
                try:
                    touch_opportunity_worker(identity, "degraded")
                except Exception:
                    pass
                time.sleep(max(1.0, opportunity_poll_seconds()))
                continue
            if not job:
                time.sleep(opportunity_poll_seconds())
                continue
            job_id = str(job.get("job_id") or "")
            try:
                touch_opportunity_worker(identity, "running", job_id)
            except Exception:
                logger.exception("OPPORTUNITY_WORKER_RUNNING_HEARTBEAT_FAILED job_id=%s", job_id)
            process_claimed_opportunity_scan_job(job, identity)
    finally:
        try:
            touch_opportunity_worker(identity, "stopped")
        except Exception:
            pass
