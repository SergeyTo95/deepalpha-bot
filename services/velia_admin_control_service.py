import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from db.database import (
    count_users,
    get_connection,
    get_user,
    get_user_analyses,
    get_users_page,
    is_subscribed,
    get_subscription_until,
    search_users,
)
from services.ai_provider_gateway import get_provider_status
from services.velia_admin_security_service import (
    ensure_velia_admin_tables,
    insert_admin_audit,
    list_admin_audit,
)


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
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


def _request_id(value: str = "") -> str:
    raw = str(value or "").strip()
    if raw and len(raw) <= 160:
        return raw
    return uuid.uuid4().hex


def _table_exists(cursor: Any, table_name: str) -> bool:
    cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
    row = cursor.fetchone()
    return bool(_row_value(row, "to_regclass", 0))


def database_health() -> Dict[str, Any]:
    started = time.monotonic()
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        row = cursor.fetchone()
        ok = int(_row_value(row, "?column?", 0, 0) or 0) == 1
        return {
            "status": "online" if ok else "error",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": None if ok else "unexpected_database_response",
        }
    except Exception as exc:
        return {
            "status": "offline",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{exc.__class__.__name__}",
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def list_users(*, query: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    safe_offset = max(0, int(offset or 0))
    q = str(query or "").strip()
    if q:
        # Existing search helper is deliberately bounded and parameterized.
        rows = search_users(q, limit=safe_limit)
        total = len(rows)
    else:
        rows = get_users_page(limit=safe_limit, offset=safe_offset)
        total = count_users()
    return {
        "items": rows,
        "total": int(total),
        "limit": safe_limit,
        "offset": safe_offset,
        "query": q,
    }


def user_detail(user_id: int) -> Optional[Dict[str, Any]]:
    user = get_user(int(user_id))
    if not user:
        return None
    try:
        subscribed = bool(is_subscribed(int(user_id)) or bool(user.get("is_vip")))
        subscription_until = get_subscription_until(int(user_id))
    except Exception:
        subscribed = None
        subscription_until = None
    try:
        recent_analyses = get_user_analyses(int(user_id), limit=10)
    except Exception:
        recent_analyses = []
    public_user = {
        key: user.get(key)
        for key in (
            "user_id", "username", "first_name", "language", "created_at", "updated_at",
            "token_balance", "is_banned", "is_vip", "total_analyses", "total_opportunities",
            "referred_by", "total_referrals", "subscription_until",
        )
        if key in user
    }
    return {
        "user": public_user,
        "subscription": {
            "active": subscribed,
            "until": subscription_until,
        },
        "recent_analyses": recent_analyses,
        "last_activity": None,
        "last_activity_reason": "canonical_user_activity_not_recorded",
    }


def _mutate_user(
    *,
    admin_user_id: int,
    user_id: int,
    action: str,
    value: Any,
    source: str,
    request_id: str = "",
    ip: str = "",
    user_agent: str = "",
) -> Dict[str, Any]:
    ensure_velia_admin_tables()
    rid = _request_id(request_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT user_id, token_balance, is_banned, is_vip FROM users WHERE user_id=%s FOR UPDATE",
            (int(user_id),),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "user_not_found", "request_id": rid}
        before = {
            "user_id": int(_row_value(row, "user_id", 0, user_id) or user_id),
            "token_balance": int(_row_value(row, "token_balance", 1, 0) or 0),
            "is_banned": bool(_row_value(row, "is_banned", 2, False)),
            "is_vip": bool(_row_value(row, "is_vip", 3, False)),
        }
        now = datetime.utcnow().isoformat()
        if action == "set_ban":
            cursor.execute(
                "UPDATE users SET is_banned=%s, updated_at=%s WHERE user_id=%s",
                (1 if bool(value) else 0, now, int(user_id)),
            )
        elif action == "set_vip":
            cursor.execute(
                "UPDATE users SET is_vip=%s, updated_at=%s WHERE user_id=%s",
                (1 if bool(value) else 0, now, int(user_id)),
            )
        elif action == "adjust_tokens":
            delta = int(value)
            if before["token_balance"] + delta < 0:
                conn.rollback()
                return {"ok": False, "error": "negative_balance_forbidden", "request_id": rid}
            cursor.execute(
                "UPDATE users SET token_balance=COALESCE(token_balance,0)+%s, updated_at=%s WHERE user_id=%s",
                (delta, now, int(user_id)),
            )
        elif action == "set_tokens":
            amount = int(value)
            if amount < 0:
                conn.rollback()
                return {"ok": False, "error": "negative_balance_forbidden", "request_id": rid}
            cursor.execute(
                "UPDATE users SET token_balance=%s, updated_at=%s WHERE user_id=%s",
                (amount, now, int(user_id)),
            )
        else:
            conn.rollback()
            return {"ok": False, "error": "unsupported_action", "request_id": rid}
        if int(cursor.rowcount or 0) != 1:
            raise RuntimeError("user_update_failed")
        cursor.execute(
            "SELECT user_id, token_balance, is_banned, is_vip FROM users WHERE user_id=%s",
            (int(user_id),),
        )
        after_row = cursor.fetchone()
        after = {
            "user_id": int(_row_value(after_row, "user_id", 0, user_id) or user_id),
            "token_balance": int(_row_value(after_row, "token_balance", 1, 0) or 0),
            "is_banned": bool(_row_value(after_row, "is_banned", 2, False)),
            "is_vip": bool(_row_value(after_row, "is_vip", 3, False)),
        }
        insert_admin_audit(
            cursor,
            admin_user_id=int(admin_user_id),
            action=f"user.{action}",
            target_type="user",
            target_id=str(int(user_id)),
            request_id=rid,
            before=before,
            after=after,
            success=True,
            source=str(source or "web")[:40],
            ip=ip,
            user_agent=user_agent,
        )
        conn.commit()
        return {"ok": True, "before": before, "after": after, "request_id": rid}
    except Exception as exc:
        conn.rollback()
        try:
            insert_admin_audit(
                cursor,
                admin_user_id=int(admin_user_id),
                action=f"user.{action}",
                target_type="user",
                target_id=str(int(user_id)),
                request_id=rid,
                before=None,
                after=None,
                success=False,
                error_code=exc.__class__.__name__,
                source=str(source or "web")[:40],
                ip=ip,
                user_agent=user_agent,
            )
            conn.commit()
        except Exception:
            conn.rollback()
        return {"ok": False, "error": "mutation_failed", "request_id": rid}
    finally:
        cursor.close()
        conn.close()


def set_user_banned(*, admin_user_id: int, user_id: int, banned: bool, source: str, **metadata: Any) -> Dict[str, Any]:
    return _mutate_user(
        admin_user_id=admin_user_id, user_id=user_id, action="set_ban",
        value=bool(banned), source=source, **metadata,
    )


def set_user_vip_status(*, admin_user_id: int, user_id: int, vip: bool, source: str, **metadata: Any) -> Dict[str, Any]:
    return _mutate_user(
        admin_user_id=admin_user_id, user_id=user_id, action="set_vip",
        value=bool(vip), source=source, **metadata,
    )


def adjust_user_tokens(*, admin_user_id: int, user_id: int, delta: int, source: str, **metadata: Any) -> Dict[str, Any]:
    return _mutate_user(
        admin_user_id=admin_user_id, user_id=user_id, action="adjust_tokens",
        value=int(delta), source=source, **metadata,
    )


def set_user_token_balance(*, admin_user_id: int, user_id: int, amount: int, source: str, **metadata: Any) -> Dict[str, Any]:
    return _mutate_user(
        admin_user_id=admin_user_id, user_id=user_id, action="set_tokens",
        value=int(amount), source=source, **metadata,
    )


def _chat_usage_snapshot(cursor: Any) -> Dict[str, Any]:
    if not _table_exists(cursor, "velia_messages"):
        return {"available": False, "reason": "velia_messages_table_missing"}
    cursor.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '1 hour') AS requests_1h,
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours') AS requests_24h,
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') AS requests_7d,
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND status='completed') AS success_24h,
          COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND status='error') AS failed_24h,
          AVG(latency_ms) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours' AND latency_ms IS NOT NULL) AS avg_latency_24h,
          COALESCE(SUM(prompt_tokens) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),0) AS input_tokens_24h,
          COALESCE(SUM(completion_tokens) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),0) AS output_tokens_24h,
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),0) AS cost_24h,
          COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),0) AS cost_7d
        FROM velia_messages
        WHERE role='assistant' AND provider IS NOT NULL
        """
    )
    row = cursor.fetchone()
    total_24 = int(_row_value(row, "requests_24h", 1, 0) or 0)
    failed_24 = int(_row_value(row, "failed_24h", 4, 0) or 0)
    return {
        "available": True,
        "requests_1h": int(_row_value(row, "requests_1h", 0, 0) or 0),
        "requests_24h": total_24,
        "requests_7d": int(_row_value(row, "requests_7d", 2, 0) or 0),
        "success_24h": int(_row_value(row, "success_24h", 3, 0) or 0),
        "failed_24h": failed_24,
        "error_rate_24h": round((failed_24 / total_24) * 100, 2) if total_24 else None,
        "avg_latency_24h_ms": round(float(_row_value(row, "avg_latency_24h", 5, 0) or 0), 1) if _row_value(row, "avg_latency_24h", 5) is not None else None,
        "input_tokens_24h": int(_row_value(row, "input_tokens_24h", 6, 0) or 0),
        "output_tokens_24h": int(_row_value(row, "output_tokens_24h", 7, 0) or 0),
        "estimated_cost_24h_usd": float(_row_value(row, "cost_24h", 8, 0) or 0),
        "estimated_cost_7d_usd": float(_row_value(row, "cost_7d", 9, 0) or 0),
    }


def _provider_breakdown(cursor: Any) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "velia_messages"):
        return []
    cursor.execute(
        """
        SELECT provider, model,
               COUNT(*) AS requests,
               COUNT(*) FILTER (WHERE status='completed') AS succeeded,
               COUNT(*) FILTER (WHERE status='error') AS failed,
               AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency_ms,
               COALESCE(SUM(prompt_tokens),0) AS input_tokens,
               COALESCE(SUM(completion_tokens),0) AS output_tokens,
               COALESCE(SUM(estimated_cost_usd),0) AS estimated_cost_usd
        FROM velia_messages
        WHERE role='assistant' AND provider IS NOT NULL
          AND created_at >= NOW() - INTERVAL '7 days'
        GROUP BY provider, model
        ORDER BY requests DESC
        LIMIT 50
        """
    )
    items: List[Dict[str, Any]] = []
    for row in cursor.fetchall() or []:
        requests_count = int(_row_value(row, "requests", 2, 0) or 0)
        failed = int(_row_value(row, "failed", 4, 0) or 0)
        items.append({
            "provider": str(_row_value(row, "provider", 0, "") or ""),
            "model": str(_row_value(row, "model", 1, "") or ""),
            "requests": requests_count,
            "succeeded": int(_row_value(row, "succeeded", 3, 0) or 0),
            "failed": failed,
            "success_rate": round(((requests_count - failed) / requests_count) * 100, 2) if requests_count else None,
            "avg_latency_ms": round(float(_row_value(row, "avg_latency_ms", 5, 0) or 0), 1) if _row_value(row, "avg_latency_ms", 5) is not None else None,
            "input_tokens": int(_row_value(row, "input_tokens", 6, 0) or 0),
            "output_tokens": int(_row_value(row, "output_tokens", 7, 0) or 0),
            "estimated_cost_usd": float(_row_value(row, "estimated_cost_usd", 8, 0) or 0),
        })
    return items


def _generation_snapshot(cursor: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "images": {"available": False, "reason": "table_missing"},
        "videos": {"available": False, "reason": "table_missing"},
    }
    if _table_exists(cursor, "velia_generated_images"):
        cursor.execute(
            """
            SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
                   COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),0),
                   COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),0)
            FROM velia_generated_images
            """
        )
        row = cursor.fetchone()
        result["images"] = {
            "available": True,
            "succeeded_24h": int(_row_value(row, "count", 0, 0) or 0),
            "succeeded_7d": int(_row_value(row, "count", 1, 0) or 0),
            "estimated_cost_24h_usd": float(_row_value(row, "coalesce", 2, 0) or 0),
            "estimated_cost_7d_usd": float(_row_value(row, "coalesce", 3, 0) or 0),
            "queued": None,
            "running": None,
            "failed": None,
            "note": "only_successful_generations_are_persisted",
        }
    if _table_exists(cursor, "velia_generated_videos"):
        cursor.execute(
            """
            SELECT COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),
                   COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),
                   COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '24 hours'),0),
                   COALESCE(SUM(estimated_cost_usd) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'),0)
            FROM velia_generated_videos
            """
        )
        row = cursor.fetchone()
        result["videos"] = {
            "available": True,
            "succeeded_24h": int(_row_value(row, "count", 0, 0) or 0),
            "succeeded_7d": int(_row_value(row, "count", 1, 0) or 0),
            "estimated_cost_24h_usd": float(_row_value(row, "coalesce", 2, 0) or 0),
            "estimated_cost_7d_usd": float(_row_value(row, "coalesce", 3, 0) or 0),
            "queued": None,
            "running": None,
            "failed": None,
            "note": "only_successful_generations_are_persisted",
        }
    return result


def memory_queue_snapshot() -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if not _table_exists(cursor, "velia_memory_shadow_outbox"):
            return {"available": False, "reason": "shadow_outbox_table_missing"}
        cursor.execute(
            """
            SELECT status, COUNT(*) AS count,
                   MAX(updated_at) AS last_updated_at
            FROM velia_memory_shadow_outbox
            GROUP BY status
            """
        )
        counts = {"pending": 0, "retrying": 0, "delivering": 0, "succeeded": 0, "failed": 0}
        last_update = None
        for row in cursor.fetchall() or []:
            status = str(_row_value(row, "status", 0, "") or "")
            if status in counts:
                counts[status] = int(_row_value(row, "count", 1, 0) or 0)
            value = _row_value(row, "last_updated_at", 2)
            if value is not None and (last_update is None or value > last_update):
                last_update = value
        return {"available": True, **counts, "last_updated_at": _iso(last_update)}
    except Exception as exc:
        return {"available": False, "reason": f"{exc.__class__.__name__}"}
    finally:
        cursor.close()
        conn.close()


def velyon_memory_health() -> Dict[str, Any]:
    endpoint = str(os.getenv("VELIA_MEMORY_ENDPOINT", "") or "").strip().rstrip("/")
    if not endpoint:
        return {"status": "unavailable", "reason": "endpoint_not_configured"}
    started = time.monotonic()
    try:
        response = requests.get(f"{endpoint}/health", timeout=(1.5, 3.0))
        latency_ms = int((time.monotonic() - started) * 1000)
        if 200 <= int(response.status_code) < 300:
            version = None
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    for key in ("version", "sha", "commit_sha", "commit"):
                        if payload.get(key):
                            version = str(payload.get(key))[:160]
                            break
            except Exception:
                pass
            return {
                "status": "online",
                "http_status": int(response.status_code),
                "latency_ms": latency_ms,
                "version": version,
            }
        return {
            "status": "degraded",
            "http_status": int(response.status_code),
            "latency_ms": latency_ms,
            "reason": f"http_{int(response.status_code)}",
        }
    except requests.RequestException as exc:
        return {
            "status": "offline",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reason": exc.__class__.__name__,
        }


def deployment_snapshot() -> Dict[str, Any]:
    def first_env(*names: str) -> Optional[str]:
        for name in names:
            value = str(os.getenv(name, "") or "").strip()
            if value:
                return value
        return None

    return {
        "service": first_env("RAILWAY_SERVICE_NAME"),
        "environment": first_env("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_ENVIRONMENT"),
        "production_branch": first_env("BOT_PRODUCTION_BRANCH") or "feature/turbo-short-term-btc",
        "deployed_branch": first_env("RAILWAY_GIT_BRANCH", "GIT_BRANCH"),
        "deployed_commit_sha": first_env("RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_COMMIT", "GIT_COMMIT_SHA"),
        "deployment_id": first_env("RAILWAY_DEPLOYMENT_ID"),
        "replica_id": first_env("RAILWAY_REPLICA_ID"),
        "application_version": first_env("APP_VERSION", "VELIA_VERSION"),
        "deployed_at": first_env("RAILWAY_DEPLOYMENT_CREATED_AT", "DEPLOYED_AT"),
        "migration_version": None,
        "migration_version_reason": "no_canonical_migration_version_source",
    }


def recent_errors(limit: int = 50) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 200))
    items: List[Dict[str, Any]] = []
    conn = get_connection()
    cursor = conn.cursor()
    try:
        if _table_exists(cursor, "velia_messages"):
            cursor.execute(
                """
                SELECT created_at, request_id, provider, model, error_code, user_id
                FROM velia_messages
                WHERE role='assistant' AND status='error'
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            for row in cursor.fetchall() or []:
                items.append({
                    "source": "velia_chat",
                    "timestamp": _iso(_row_value(row, "created_at", 0)),
                    "request_id": _row_value(row, "request_id", 1),
                    "provider": _row_value(row, "provider", 2),
                    "model": _row_value(row, "model", 3),
                    "error": _row_value(row, "error_code", 4),
                    "user_id": _row_value(row, "user_id", 5),
                })
        if _table_exists(cursor, "velia_memory_shadow_outbox"):
            cursor.execute(
                """
                SELECT updated_at, event_id, response_status, last_error, user_id, status
                FROM velia_memory_shadow_outbox
                WHERE status IN ('failed','retrying') AND last_error IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            for row in cursor.fetchall() or []:
                items.append({
                    "source": "velyon_memory",
                    "timestamp": _iso(_row_value(row, "updated_at", 0)),
                    "request_id": _row_value(row, "event_id", 1),
                    "http_status": _row_value(row, "response_status", 2),
                    "error": _row_value(row, "last_error", 3),
                    "user_id": _row_value(row, "user_id", 4),
                    "status": _row_value(row, "status", 5),
                })
    except Exception as exc:
        items.append({
            "source": "control_center",
            "timestamp": None,
            "error": f"error_query_unavailable:{exc.__class__.__name__}",
        })
    finally:
        cursor.close()
        conn.close()
    items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return items[:safe_limit]


def ai_snapshot() -> Dict[str, Any]:
    providers = get_provider_status()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        usage = _chat_usage_snapshot(cursor)
        breakdown = _provider_breakdown(cursor)
    except Exception as exc:
        usage = {"available": False, "reason": exc.__class__.__name__}
        breakdown = []
    finally:
        cursor.close()
        conn.close()
    return {
        "routing": providers,
        "usage": usage,
        "provider_model_breakdown_7d": breakdown,
        "provider_live_health": None,
        "provider_live_health_reason": "no_nonbillable_provider_health_contract",
    }


def overview_snapshot() -> Dict[str, Any]:
    db_health = database_health()
    memory_health = velyon_memory_health()
    queue = memory_queue_snapshot()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        chat = _chat_usage_snapshot(cursor)
        generations = _generation_snapshot(cursor)
    except Exception as exc:
        chat = {"available": False, "reason": exc.__class__.__name__}
        generations = {
            "images": {"available": False, "reason": exc.__class__.__name__},
            "videos": {"available": False, "reason": exc.__class__.__name__},
        }
    finally:
        cursor.close()
        conn.close()
    try:
        users_total = count_users()
        users_available = True
    except Exception:
        users_total = None
        users_available = False
    return {
        "velia_status": "online" if db_health.get("status") == "online" else "degraded",
        "backend": {"status": "online", "source": "current_admin_request"},
        "database": db_health,
        "velyon_core": {
            "status": "online" if db_health.get("status") == "online" else "degraded",
            "source": "backend_process_and_database",
        },
        "velyon_memory": {**memory_health, "queue": queue},
        "users": {
            "total": users_total if users_available else None,
            "active_24h": None,
            "active_24h_reason": "canonical_user_activity_not_recorded",
        },
        "http_requests": {
            "available": False,
            "reason": "no_canonical_http_request_telemetry",
        },
        "ai": chat,
        "generations": generations,
        "deploy": deployment_snapshot(),
        "background_jobs": {
            "velyon_memory_shadow": queue,
            "other_jobs": None,
            "other_jobs_reason": "no_canonical_background_job_registry",
        },
        "recent_errors": recent_errors(limit=10),
    }


def audit_snapshot(limit: int = 100) -> List[Dict[str, Any]]:
    return list_admin_audit(limit=limit)
