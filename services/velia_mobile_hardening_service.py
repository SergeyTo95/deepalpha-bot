import asyncio
import os
from typing import Any, Callable, Dict, Optional

from aiohttp import web


_MESSAGE_ROUTE = "/mobile-api/v1/conversations/{conversation_id}/messages"
_LOCK_NAME_PREFIX = "deepalpha:velia:generation:user:"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _first_value(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    try:
        return row[0]
    except (IndexError, TypeError):
        return None


def _lock_name(user_id: int) -> str:
    return f"{_LOCK_NAME_PREFIX}{int(user_id)}"


def _try_user_generation_lock(cursor: Any, user_id: int) -> bool:
    cursor.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
        (_lock_name(user_id),),
    )
    return bool(_first_value(cursor.fetchone()))


def _release_user_generation_lock(cursor: Any, user_id: int) -> None:
    cursor.execute(
        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
        (_lock_name(user_id),),
    )
    cursor.fetchone()


def _expire_abandoned_pending(cursor: Any, user_id: int) -> None:
    lease_seconds = _env_int(
        "VELIA_CHAT_PENDING_LEASE_SECONDS",
        600,
        60,
        3600,
    )
    cursor.execute(
        """
        UPDATE velia_messages
        SET status='error', error_code='generation_abandoned', updated_at=NOW()
        WHERE user_id=%s AND role='assistant' AND status='pending'
          AND deleted_at IS NULL
          AND updated_at < NOW() - (%s * INTERVAL '1 second')
        """,
        (int(user_id), lease_seconds),
    )


def build_hardened_send_message(
    chat_module: Any,
    original_send_message: Callable[..., Dict[str, Any]],
) -> Callable[..., Dict[str, Any]]:
    def hardened_send_message(
        user_id: int,
        conversation_id: str,
        content: str,
        *,
        idempotency_key: str,
        attachment_ids: Any = None,
    ) -> Dict[str, Any]:
        lock_conn = chat_module.get_connection()
        lock_cursor = chat_module._dict_cursor(lock_conn)
        acquired = False
        try:
            acquired = _try_user_generation_lock(lock_cursor, int(user_id))
            if not acquired:
                return {"ok": False, "error": "generation_in_progress"}

            # Keep idempotency and attachment-set comparison inside the final
            # attachment-aware sender. Returning a duplicate here would bypass
            # its protection against reusing one key with a different file set.
            _expire_abandoned_pending(lock_cursor, int(user_id))
            lock_conn.commit()

            # Keep the session-level advisory lock for the complete provider call.
            # A second process cannot pass the per-user pending/spend gate while the
            # first physical request is still running.
            return original_send_message(
                int(user_id),
                str(conversation_id),
                str(content),
                idempotency_key=str(idempotency_key),
                attachment_ids=attachment_ids,
            )
        finally:
            try:
                lock_conn.rollback()
            except Exception:
                pass
            if acquired:
                try:
                    _release_user_generation_lock(lock_cursor, int(user_id))
                    lock_conn.commit()
                except Exception:
                    try:
                        lock_conn.rollback()
                    except Exception:
                        pass
            try:
                lock_cursor.close()
            finally:
                lock_conn.close()

    return hardened_send_message


def build_latest_messages_reader(
    chat_module: Any,
) -> Callable[..., Optional[list[Dict[str, Any]]]]:
    def list_latest_messages(
        user_id: int,
        conversation_id: str,
        *,
        limit: int = 100,
    ) -> Optional[list[Dict[str, Any]]]:
        bounded_limit = min(200, max(1, int(limit or 100)))
        debug_usage = chat_module.is_debug_usage_enabled_for_user(user_id)
        conn = chat_module.get_connection()
        cursor = chat_module._dict_cursor(conn)
        try:
            cursor.execute(
                "SELECT 1 FROM velia_conversations "
                "WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL",
                (str(conversation_id), int(user_id)),
            )
            if not cursor.fetchone():
                return None
            cursor.execute(
                """
                SELECT message_id, conversation_id, user_id, role, content, status,
                       idempotency_key, reply_to_message_id, request_id, provider, model,
                       prompt_tokens, completion_tokens, total_tokens,
                       cached_input_tokens, reasoning_tokens, estimated_cost_usd,
                       latency_ms, error_code, created_at, updated_at
                FROM (
                    SELECT message_id, conversation_id, user_id, role, content, status,
                           idempotency_key, reply_to_message_id, request_id, provider, model,
                           prompt_tokens, completion_tokens, total_tokens,
                           cached_input_tokens, reasoning_tokens, estimated_cost_usd,
                           latency_ms, error_code, created_at, updated_at
                    FROM velia_messages
                    WHERE conversation_id=%s AND user_id=%s AND deleted_at IS NULL
                    ORDER BY created_at DESC,
                             CASE role
                                 WHEN 'user' THEN 0
                                 WHEN 'assistant' THEN 1
                                 ELSE 2
                             END ASC,
                             message_id DESC
                    LIMIT %s
                ) AS recent_messages
                ORDER BY created_at ASC,
                         CASE role
                             WHEN 'user' THEN 0
                             WHEN 'assistant' THEN 1
                             ELSE 2
                         END ASC,
                         message_id ASC
                """,
                (str(conversation_id), int(user_id), bounded_limit),
            )
            return [
                chat_module._serialize_message(row, debug_usage=debug_usage)
                for row in cursor.fetchall() or []
            ]
        finally:
            cursor.close()
            conn.close()

    return list_latest_messages


def _route_canonical(route: Any) -> str:
    resource = getattr(route, "resource", None)
    canonical = str(getattr(resource, "canonical", "") or "")
    if canonical:
        return canonical
    try:
        info = route.get_info() or {}
    except Exception:
        info = {}
    return str(info.get("formatter") or info.get("path") or "")


def replace_blocking_message_handler(app: web.Application, routes_module: Any) -> None:
    async def handle_messages_send(request: web.Request) -> web.Response:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        auth = routes_module._require_mobile_auth(request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        data = await routes_module._read_json(request)
        if data is None:
            return routes_module._json_response(
                {"ok": False, "error": "invalid_json"},
                status=400,
            )
        idempotency_key = str(
            request.headers.get("Idempotency-Key")
            or data.get("idempotency_key")
            or ""
        ).strip()
        result = await asyncio.to_thread(
            routes_module.send_message,
            int(auth["user_id"]),
            request.match_info["conversation_id"],
            str(data.get("content") or ""),
            idempotency_key=idempotency_key,
            attachment_ids=data.get("attachment_ids"),
        )
        if result.get("ok"):
            return routes_module._json_response(
                result,
                status=200 if result.get("duplicate") else 201,
            )
        error = str(result.get("error") or "generation_failed")
        status = 400
        if error in {"conversation_not_found", "attachment_not_found"}:
            status = 404
        elif error in {
            "generation_in_progress",
            "idempotency_attachment_mismatch",
        } or result.get("pending"):
            status = 409
        elif error.endswith("limit_exceeded"):
            status = 429
        elif error == "velia_chat_disabled":
            status = 503
        elif error in {
            "timeout",
            "connection_error",
            "rate_limit",
            "server_error",
            "empty_200",
            "json_parse_error",
            "generation_exception",
            "generation_failed",
        }:
            status = 502
        return routes_module._json_response(result, status=status)

    for route in app.router.routes():
        if str(getattr(route, "method", "")).upper() != "POST":
            continue
        if _route_canonical(route) != _MESSAGE_ROUTE:
            continue
        route._handler = handle_messages_send
        return
    raise RuntimeError("VELIA mobile message route is unavailable")


def install(app: web.Application, chat_module: Any, routes_module: Any) -> None:
    if app.get("velia_mobile_hardening_installed"):
        return

    hardened_send_message = build_hardened_send_message(
        chat_module,
        chat_module.send_message,
    )
    latest_messages_reader = build_latest_messages_reader(chat_module)

    chat_module.send_message = hardened_send_message
    chat_module.list_messages = latest_messages_reader
    routes_module.send_message = hardened_send_message
    routes_module.list_messages = latest_messages_reader
    replace_blocking_message_handler(app, routes_module)
    app["velia_mobile_hardening_installed"] = True
