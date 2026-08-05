from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import quote, urlencode

import requests

from db.database import get_connection
from services import velia_agent_connector_crypto_service as crypto
from services import velia_agent_job_service as jobs
from services import velia_agent_tool_registry_service as registry
from services.velia_agent_protocol_service import ActionRisk, AgentProtocolError

_CONNECTOR = "google_calendar"
_SCOPE = "https://www.googleapis.com/auth/calendar.events"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()
_TOOLS_READY = False


class GoogleCalendarError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(code)
        self.code = str(code)
        self.status = int(status)
        self.detail = str(detail or "")[:300]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def enabled() -> bool:
    return _env_bool("VELIA_GOOGLE_CALENDAR_ENABLED", False)


def _client_id() -> str:
    return str(os.getenv("VELIA_GOOGLE_OAUTH_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return str(os.getenv("VELIA_GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()


def _callback_url() -> str:
    value = str(os.getenv("VELIA_GOOGLE_OAUTH_CALLBACK_URL") or "").strip()
    return value if value.startswith("https://") else ""


def configured() -> bool:
    return bool(enabled() and _client_id() and _client_secret() and _callback_url() and crypto.configured())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _value(row: Any, key: str, index: int = 0, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    if row is None:
        return default
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def _require_configured() -> None:
    if not enabled():
        raise GoogleCalendarError("velia_google_calendar_disabled", status=503)
    if not configured():
        raise GoogleCalendarError("velia_google_calendar_not_configured", status=503)


def ensure_google_calendar_tables() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        jobs.ensure_velia_agent_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    connector TEXT NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    consumed_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS velia_agent_connector_credentials (
                    connector_account_id TEXT PRIMARY KEY,
                    access_token_ciphertext TEXT NOT NULL,
                    refresh_token_ciphertext TEXT NOT NULL,
                    token_expires_at TIMESTAMP NOT NULL,
                    scopes_json TEXT NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_velia_agent_oauth_states_expiry "
                "ON velia_agent_oauth_states(connector, expires_at)"
            )
            conn.commit()
            _SCHEMA_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _state_hash(state: str) -> str:
    return hashlib.sha256(str(state).encode("utf-8")).hexdigest()


def create_authorization_url(user_id: int) -> Dict[str, Any]:
    _require_configured()
    ensure_google_calendar_tables()
    state = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM velia_agent_oauth_states WHERE connector=%s AND (expires_at<=%s OR consumed_at IS NOT NULL)",
            (_CONNECTOR, now),
        )
        cursor.execute(
            "INSERT INTO velia_agent_oauth_states (state_hash,user_id,connector,expires_at,created_at) VALUES (%s,%s,%s,%s,%s)",
            (_state_hash(state), int(user_id), _CONNECTOR, now + timedelta(minutes=10), now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    query = urlencode(
        {
            "client_id": _client_id(),
            "redirect_uri": _callback_url(),
            "response_type": "code",
            "scope": _SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return {"url": f"{_AUTH_URL}?{query}", "expires_in": 600}


def _consume_state(state: str) -> int:
    ensure_google_calendar_tables()
    normalized = str(state or "").strip()
    if not normalized:
        raise GoogleCalendarError("velia_google_oauth_state_invalid", status=400)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_agent_oauth_states
            SET consumed_at=%s
            WHERE state_hash=%s
              AND connector=%s
              AND consumed_at IS NULL
              AND expires_at>%s
            RETURNING user_id
            """,
            (datetime.utcnow(), _state_hash(normalized), _CONNECTOR, datetime.utcnow()),
        )
        row = cursor.fetchone()
        if not row:
            raise GoogleCalendarError("velia_google_oauth_state_invalid", status=400)
        user_id = int(_value(row, "user_id", 0, 0) or 0)
        conn.commit()
        return user_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    expected: Tuple[int, ...] = (200,),
) -> Tuple[int, Dict[str, Any]]:
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            json=body,
            timeout=(5, 25),
        )
    except requests.RequestException as exc:
        raise GoogleCalendarError("velia_google_network_error", status=502) from exc
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    if response.status_code not in expected:
        raise GoogleCalendarError(
            "velia_google_api_error",
            status=502,
            detail=f"http_{response.status_code}",
        )
    return response.status_code, payload if isinstance(payload, dict) else {}


def _exchange_code(code: str) -> Dict[str, Any]:
    normalized = str(code or "").strip()
    if not normalized:
        raise GoogleCalendarError("velia_google_oauth_code_missing", status=400)
    _, payload = _request_json(
        "POST",
        _TOKEN_URL,
        data={
            "code": normalized,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _callback_url(),
            "grant_type": "authorization_code",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not access_token:
        raise GoogleCalendarError("velia_google_access_token_missing", status=502)
    if not refresh_token:
        raise GoogleCalendarError("velia_google_refresh_token_missing", status=409)
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": max(60, int(payload.get("expires_in") or 3600)),
        "scope": str(payload.get("scope") or _SCOPE),
    }


def _primary_calendar(access_token: str) -> Dict[str, Any]:
    _, payload = _request_json(
        "GET",
        f"{_CALENDAR_API}/calendars/primary",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    calendar_id = str(payload.get("id") or "").strip()
    if not calendar_id:
        raise GoogleCalendarError("velia_google_primary_calendar_missing", status=502)
    return {
        "id": calendar_id,
        "summary": str(payload.get("summary") or "")[:300],
        "time_zone": str(payload.get("timeZone") or "")[:100],
    }


def connect_with_code(state: str, code: str) -> Dict[str, Any]:
    _require_configured()
    user_id = _consume_state(state)
    tokens = _exchange_code(code)
    calendar = _primary_calendar(tokens["access_token"])
    account_id = str(uuid.uuid4())
    now = datetime.utcnow()
    metadata = {
        "summary": calendar["summary"],
        "time_zone": calendar["time_zone"],
        "scope": _SCOPE,
    }
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO velia_agent_connector_accounts (
                connector_account_id,user_id,connector,external_account_id,status,metadata_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (user_id,connector,external_account_id) DO UPDATE SET
                status=EXCLUDED.status,
                metadata_json=EXCLUDED.metadata_json,
                updated_at=EXCLUDED.updated_at
            RETURNING connector_account_id
            """,
            (
                account_id,
                int(user_id),
                _CONNECTOR,
                calendar["id"],
                "active",
                _json(metadata),
                now,
                now,
            ),
        )
        row = cursor.fetchone()
        connector_account_id = str(_value(row, "connector_account_id", 0, account_id))
        cursor.execute(
            """
            INSERT INTO velia_agent_connector_credentials (
                connector_account_id,access_token_ciphertext,refresh_token_ciphertext,
                token_expires_at,scopes_json,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (connector_account_id) DO UPDATE SET
                access_token_ciphertext=EXCLUDED.access_token_ciphertext,
                refresh_token_ciphertext=EXCLUDED.refresh_token_ciphertext,
                token_expires_at=EXCLUDED.token_expires_at,
                scopes_json=EXCLUDED.scopes_json,
                updated_at=EXCLUDED.updated_at
            """,
            (
                connector_account_id,
                crypto.encrypt_secret(tokens["access_token"]),
                crypto.encrypt_secret(tokens["refresh_token"]),
                now + timedelta(seconds=int(tokens["expires_in"])),
                _json(str(tokens["scope"]).split()),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    jobs.audit(
        user_id,
        "connector_connected",
        payload={"connector": _CONNECTOR, "connector_account_id": connector_account_id},
    )
    return {
        "user_id": int(user_id),
        "connector_account_id": connector_account_id,
        "connector": _CONNECTOR,
        "summary": calendar["summary"],
        "time_zone": calendar["time_zone"],
    }


def _credential_row(user_id: int) -> Dict[str, Any]:
    ensure_google_calendar_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                a.connector_account_id,a.external_account_id,a.metadata_json,
                c.access_token_ciphertext,c.refresh_token_ciphertext,c.token_expires_at,c.scopes_json
            FROM velia_agent_connector_accounts AS a
            JOIN velia_agent_connector_credentials AS c
              ON c.connector_account_id=a.connector_account_id
            WHERE a.user_id=%s AND a.connector=%s AND a.status='active'
            ORDER BY a.updated_at DESC
            LIMIT 1
            """,
            (int(user_id), _CONNECTOR),
        )
        row = cursor.fetchone()
        if not row:
            raise GoogleCalendarError("velia_google_calendar_not_connected", status=409)
        columns = [
            "connector_account_id",
            "external_account_id",
            "metadata_json",
            "access_token_ciphertext",
            "refresh_token_ciphertext",
            "token_expires_at",
            "scopes_json",
        ]
        if isinstance(row, dict):
            return dict(row)
        return {name: row[index] for index, name in enumerate(columns)}
    finally:
        cursor.close()
        conn.close()


def _as_utc_naive(value: Any) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _refresh_access_token(user_id: int, row: Dict[str, Any]) -> str:
    refresh_token = crypto.decrypt_secret(str(row.get("refresh_token_ciphertext") or ""))
    _, payload = _request_json(
        "POST",
        _TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
        },
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GoogleCalendarError("velia_google_access_token_missing", status=502)
    expires_at = datetime.utcnow() + timedelta(seconds=max(60, int(payload.get("expires_in") or 3600)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE velia_agent_connector_credentials
            SET access_token_ciphertext=%s,token_expires_at=%s,updated_at=%s
            WHERE connector_account_id=%s
            """,
            (
                crypto.encrypt_secret(access_token),
                expires_at,
                datetime.utcnow(),
                str(row["connector_account_id"]),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    jobs.audit(user_id, "connector_token_refreshed", payload={"connector": _CONNECTOR})
    return access_token


def _access_token(user_id: int, *, force_refresh: bool = False) -> str:
    row = _credential_row(user_id)
    expires_at = _as_utc_naive(row.get("token_expires_at"))
    if force_refresh or expires_at <= datetime.utcnow() + timedelta(seconds=60):
        return _refresh_access_token(user_id, row)
    return crypto.decrypt_secret(str(row.get("access_token_ciphertext") or ""))


def _calendar_request(
    user_id: int,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    expected: Tuple[int, ...] = (200,),
) -> Tuple[int, Dict[str, Any]]:
    url = f"{_CALENDAR_API}{path}"
    last_error: Optional[GoogleCalendarError] = None
    for attempt in range(2):
        token = _access_token(user_id, force_refresh=attempt == 1)
        try:
            return _request_json(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                params=params,
                body=body,
                expected=expected,
            )
        except GoogleCalendarError as exc:
            last_error = exc
            if exc.detail != "http_401" or attempt == 1:
                raise
    raise last_error or GoogleCalendarError("velia_google_api_error", status=502)


def _parse_rfc3339(value: Any, field: str) -> datetime:
    text = str(value or "").strip()
    if not text or len(text) > 80:
        raise GoogleCalendarError(f"velia_google_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GoogleCalendarError(f"velia_google_{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise GoogleCalendarError(f"velia_google_{field}_timezone_required")
    return parsed


def _event_view(item: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "title": str(item.get("summary") or "")[:300],
        "start": dict(item.get("start") or {}),
        "end": dict(item.get("end") or {}),
        "location": str(item.get("location") or "")[:500],
        "status": str(item.get("status") or ""),
        "html_url": str(item.get("htmlLink") or ""),
    }


def list_events(user_id: int, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    time_min = str(arguments.get("time_min") or now.isoformat().replace("+00:00", "Z"))
    time_max = str(
        arguments.get("time_max")
        or (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    )
    start = _parse_rfc3339(time_min, "time_min")
    end = _parse_rfc3339(time_max, "time_max")
    if end <= start:
        raise GoogleCalendarError("velia_google_time_range_invalid")
    max_results = max(1, min(50, int(arguments.get("max_results") or 20)))
    _, payload = _calendar_request(
        int(user_id),
        "GET",
        "/calendars/primary/events",
        params={
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": max_results,
        },
    )
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    return {"items": [_event_view(item) for item in items if isinstance(item, dict)][:max_results]}


def _event_id(idempotency_key: str) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        raise GoogleCalendarError("velia_google_idempotency_key_missing")
    return "velia" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def create_event(user_id: int, arguments: Mapping[str, Any]) -> Dict[str, Any]:
    title = str(arguments.get("title") or "").strip()[:300]
    if not title:
        raise GoogleCalendarError("velia_google_event_title_required")
    start_text = str(arguments.get("start") or "").strip()
    end_text = str(arguments.get("end") or "").strip()
    start = _parse_rfc3339(start_text, "event_start")
    end = _parse_rfc3339(end_text, "event_end")
    if end <= start:
        raise GoogleCalendarError("velia_google_event_range_invalid")
    time_zone = str(arguments.get("time_zone") or "").strip()[:100]
    body: Dict[str, Any] = {
        "id": _event_id(str(arguments.get("_velia_idempotency_key") or "")),
        "summary": title,
        "start": {"dateTime": start_text},
        "end": {"dateTime": end_text},
    }
    if time_zone:
        body["start"]["timeZone"] = time_zone
        body["end"]["timeZone"] = time_zone
    description = str(arguments.get("description") or "").strip()[:4000]
    location = str(arguments.get("location") or "").strip()[:500]
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    status, payload = _calendar_request(
        int(user_id),
        "POST",
        "/calendars/primary/events",
        params={"sendUpdates": "none"},
        body=body,
        expected=(200, 201, 409),
    )
    if status == 409:
        _, payload = _calendar_request(
            int(user_id),
            "GET",
            f"/calendars/primary/events/{quote(body['id'], safe='')}",
        )
    return {"event": _event_view(payload), "idempotent": status == 409}


def connection_status(user_id: int) -> Dict[str, Any]:
    if not enabled():
        return {"enabled": False, "configured": False, "connected": False}
    if not configured():
        return {"enabled": True, "configured": False, "connected": False}
    ensure_google_calendar_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT connector_account_id,external_account_id,metadata_json,status
            FROM velia_agent_connector_accounts
            WHERE user_id=%s AND connector=%s AND status='active'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (int(user_id), _CONNECTOR),
        )
        row = cursor.fetchone()
        if not row:
            return {"enabled": True, "configured": True, "connected": False}
        metadata = _loads(_value(row, "metadata_json", 2, "{}"), {})
        return {
            "enabled": True,
            "configured": True,
            "connected": True,
            "connector_account_id": str(_value(row, "connector_account_id", 0, "")),
            "calendar_id": str(_value(row, "external_account_id", 1, "")),
            "summary": str(metadata.get("summary") or ""),
            "time_zone": str(metadata.get("time_zone") or ""),
        }
    finally:
        cursor.close()
        conn.close()


def disconnect(user_id: int) -> None:
    ensure_google_calendar_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT connector_account_id FROM velia_agent_connector_accounts WHERE user_id=%s AND connector=%s",
            (int(user_id), _CONNECTOR),
        )
        account_ids = [str(_value(row, "connector_account_id", 0, "")) for row in (cursor.fetchall() or [])]
        if account_ids:
            cursor.execute(
                "DELETE FROM velia_agent_connector_credentials WHERE connector_account_id = ANY(%s)",
                (account_ids,),
            )
        cursor.execute(
            "DELETE FROM velia_agent_connector_accounts WHERE user_id=%s AND connector=%s",
            (int(user_id), _CONNECTOR),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    jobs.audit(user_id, "connector_disconnected", payload={"connector": _CONNECTOR})


def register_tools() -> None:
    global _TOOLS_READY
    if _TOOLS_READY or not configured():
        return
    definitions = (
        registry.ToolDefinition(
            "google.calendar.events.list",
            "Read the user's connected Google Calendar events for a bounded time range.",
            ActionRisk.READ,
            list_events,
            connector=_CONNECTOR,
        ),
        registry.ToolDefinition(
            "google.calendar.events.create",
            "Create one Google Calendar event after explicit user approval.",
            ActionRisk.WRITE_EXTERNAL,
            create_event,
            connector=_CONNECTOR,
        ),
    )
    for definition in definitions:
        try:
            registry.register_tool(definition)
        except AgentProtocolError as exc:
            if exc.code != "velia_agent_tool_duplicate":
                raise
    _TOOLS_READY = True
