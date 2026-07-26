import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from db.database import get_connection
from services.developer_api_observability_service import ensure_api_observability_tables

logger = logging.getLogger(__name__)

SUPPORTED_WEBHOOK_EVENTS = {"analysis.completed", "analysis.failed"}
_WEBHOOK_TABLES_READY = False


class WebhookError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = str(code)
        self.details = details


def _row_to_dict(cursor, row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, row))


def _rows_to_dicts(cursor, rows) -> List[Dict[str, Any]]:
    return [item for item in (_row_to_dict(cursor, row) for row in rows or []) if item]


def _safe_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def webhook_max_per_client() -> int:
    return _safe_int("API_WEBHOOK_MAX_PER_CLIENT", 5, 1, 50)


def webhook_timeout_seconds() -> int:
    return _safe_int("API_WEBHOOK_TIMEOUT_SECONDS", 10, 2, 30)


def webhook_max_attempts() -> int:
    return _safe_int("API_WEBHOOK_MAX_ATTEMPTS", 6, 1, 12)


def webhook_disable_after_failures() -> int:
    return _safe_int("API_WEBHOOK_DISABLE_AFTER_FAILURES", 20, 3, 1000)


def webhook_worker_stale_seconds() -> int:
    return _safe_int("API_WEBHOOK_WORKER_STALE_SECONDS", 90, 15, 900)


def webhook_queue_warning_size() -> int:
    return _safe_int("API_WEBHOOK_QUEUE_WARNING_SIZE", 50, 1, 100000)


def webhook_queue_warning_age_seconds() -> int:
    return _safe_int("API_WEBHOOK_QUEUE_WARNING_AGE_SECONDS", 300, 15, 86400)


def webhook_poll_seconds() -> float:
    try:
        value = float(str(os.getenv("API_WEBHOOK_POLL_SECONDS", "1") or "1"))
    except Exception:
        value = 1.0
    return max(0.2, min(value, 30.0))


def webhook_master_key() -> bytes:
    raw = str(
        os.getenv("WEBHOOK_SIGNING_MASTER_KEY")
        or os.getenv("API_WEBHOOK_MASTER_KEY")
        or os.getenv("ADMIN_SECRET_KEY")
        or ""
    ).strip()
    if len(raw) < 16:
        raise WebhookError("webhook_signing_key_not_configured")
    return hashlib.sha256(("deepalpha-webhooks-v1:" + raw).encode("utf-8")).digest()


def normalize_webhook_events(events: Optional[Iterable[str]]) -> List[str]:
    source = list(events or sorted(SUPPORTED_WEBHOOK_EVENTS))
    result: List[str] = []
    for raw in source:
        event = str(raw or "").strip().lower()
        if event in SUPPORTED_WEBHOOK_EVENTS and event not in result:
            result.append(event)
    if not result:
        raise WebhookError("at_least_one_webhook_event_required")
    return result


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(address.is_global)


def resolve_public_webhook_target(hostname: str, port: int = 443) -> List[str]:
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise WebhookError("webhook_target_not_public")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise WebhookError("webhook_target_not_public")
        return [str(literal)]

    try:
        records = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebhookError("webhook_dns_resolution_failed") from exc
    addresses: List[str] = []
    for record in records:
        address = str(record[4][0]).split("%", 1)[0]
        if not _is_public_ip(address):
            raise WebhookError("webhook_target_not_public")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise WebhookError("webhook_dns_resolution_failed")
    return addresses


def validate_webhook_url(value: Any, *, resolve_dns: bool = True) -> Dict[str, Any]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 1000:
        raise WebhookError("invalid_webhook_url")
    try:
        parsed = urlsplit(raw)
    except Exception as exc:
        raise WebhookError("invalid_webhook_url") from exc
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise WebhookError("invalid_webhook_url")
    if parsed.username or parsed.password:
        raise WebhookError("invalid_webhook_url")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise WebhookError("invalid_webhook_url") from exc
    if port != 443:
        raise WebhookError("webhook_port_not_allowed", allowed_ports=[443])
    host = str(parsed.hostname).lower().rstrip(".")
    path = parsed.path or "/"
    if not path.startswith("/"):
        raise WebhookError("invalid_webhook_url")
    canonical = f"https://{host}{path}"
    if parsed.query:
        canonical += f"?{parsed.query}"
    addresses = resolve_public_webhook_target(host, port) if resolve_dns else []
    return {
        "url": canonical,
        "host": host,
        "port": port,
        "path": path + (f"?{parsed.query}" if parsed.query else ""),
        "addresses": addresses,
    }


def derive_webhook_secret(client_id: int, webhook_id: str, secret_salt: str) -> str:
    message = f"v1:{int(client_id)}:{str(webhook_id)}:{str(secret_salt)}".encode("utf-8")
    digest = hmac.new(webhook_master_key(), message, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"whsec_{encoded}"


def hash_webhook_secret(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()


def sign_webhook_payload(secret: str, timestamp: str, body: bytes) -> str:
    signed = str(timestamp).encode("ascii") + b"." + bytes(body)
    digest = hmac.new(str(secret).encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_webhook_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign_webhook_payload(secret, timestamp, body), str(signature or ""))


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(type(value).__name__)


def ensure_api_webhook_tables() -> None:
    global _WEBHOOK_TABLES_READY
    if _WEBHOOK_TABLES_READY:
        return
    ensure_api_observability_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS webhook_id TEXT")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'default'")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS secret_salt TEXT")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS secret_version INTEGER NOT NULL DEFAULT 1")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS last_failure_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_webhooks ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMP")
        cursor.execute("UPDATE api_webhooks SET status='disabled', disabled_at=COALESCE(disabled_at, NOW()) WHERE secret_salt IS NULL")
        cursor.execute("UPDATE api_webhooks SET webhook_id='wh_' || md5(id::text || random()::text || clock_timestamp()::text) WHERE webhook_id IS NULL")
        cursor.execute("UPDATE api_webhooks SET secret_salt=md5(id::text || random()::text || clock_timestamp()::text) WHERE secret_salt IS NULL")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_webhooks_public_id ON api_webhooks(webhook_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_webhooks_client_status ON api_webhooks(client_id, status)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_webhook_deliveries (
                delivery_id TEXT PRIMARY KEY,
                webhook_id BIGINT NOT NULL REFERENCES api_webhooks(id) ON DELETE CASCADE,
                client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                job_id TEXT NOT NULL REFERENCES api_jobs(job_id) ON DELETE CASCADE,
                event TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                manual_retry_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TIMESTAMP NOT NULL DEFAULT NOW(),
                locked_by TEXT,
                lease_until TIMESTAMP,
                response_status INTEGER,
                response_body_snippet TEXT,
                last_error TEXT,
                delivered_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(webhook_id, job_id, event)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_webhook_delivery_attempts (
                id BIGSERIAL PRIMARY KEY,
                delivery_id TEXT NOT NULL REFERENCES api_webhook_deliveries(delivery_id) ON DELETE CASCADE,
                attempt_sequence INTEGER NOT NULL,
                request_timestamp TEXT NOT NULL,
                resolved_ip TEXT,
                response_status INTEGER,
                success BOOLEAN NOT NULL DEFAULT FALSE,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                response_body_snippet TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_ready ON api_webhook_deliveries(status, next_attempt_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_client ON api_webhook_deliveries(client_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_webhook_attempts_delivery ON api_webhook_delivery_attempts(delivery_id, id DESC)")
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION enqueue_deepalpha_webhook_delivery()
            RETURNS trigger AS $$
            DECLARE
                v_event TEXT;
                v_hook RECORD;
                v_delivery_id TEXT;
                v_reservation_status TEXT;
                v_payload JSONB;
            BEGIN
                IF NEW.job_type <> 'quick_analysis'
                   OR NEW.status NOT IN ('success', 'error')
                   OR OLD.status IS NOT DISTINCT FROM NEW.status THEN
                    RETURN NEW;
                END IF;
                v_event := CASE WHEN NEW.status='success' THEN 'analysis.completed' ELSE 'analysis.failed' END;
                SELECT status INTO v_reservation_status FROM api_credit_reservations WHERE job_id=NEW.job_id LIMIT 1;
                FOR v_hook IN
                    SELECT id, client_id
                    FROM api_webhooks
                    WHERE client_id=NEW.client_id
                      AND status='active'
                      AND v_event = ANY(string_to_array(events, ','))
                LOOP
                    v_delivery_id := 'delivery_' || md5(random()::text || clock_timestamp()::text || v_hook.id::text || NEW.job_id);
                    v_payload := jsonb_build_object(
                        'event', v_event,
                        'delivery_id', v_delivery_id,
                        'created_at', to_char(timezone('UTC', NOW()), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                        'data', jsonb_build_object(
                            'job_id', NEW.job_id,
                            'status', NEW.status,
                            'analysis_type', 'quick',
                            'result', CASE WHEN NEW.status='success' THEN COALESCE(NEW.result_json::jsonb, '{}'::jsonb) ELSE NULL END,
                            'error', CASE WHEN NEW.status='error' THEN COALESCE(NEW.error, 'analysis_failed') ELSE NULL END,
                            'credits', jsonb_build_object(
                                'reserved', NEW.units_reserved,
                                'charged', NEW.units_charged,
                                'reservation_status', v_reservation_status
                            )
                        )
                    );
                    INSERT INTO api_webhook_deliveries (
                        delivery_id, webhook_id, client_id, job_id, event, payload_json
                    ) VALUES (
                        v_delivery_id, v_hook.id, NEW.client_id, NEW.job_id, v_event, v_payload::text
                    ) ON CONFLICT (webhook_id, job_id, event) DO NOTHING;
                END LOOP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cursor.execute("DROP TRIGGER IF EXISTS trg_enqueue_deepalpha_webhook_delivery ON api_jobs")
        cursor.execute(
            """
            CREATE TRIGGER trg_enqueue_deepalpha_webhook_delivery
            AFTER UPDATE OF status ON api_jobs
            FOR EACH ROW EXECUTE FUNCTION enqueue_deepalpha_webhook_delivery()
            """
        )
        conn.commit()
        _WEBHOOK_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _public_webhook(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "webhook_id": str(row.get("webhook_id") or ""),
        "name": str(row.get("name") or "default"),
        "url": str(row.get("url") or ""),
        "events": [item for item in str(row.get("events") or "").split(",") if item],
        "status": str(row.get("status") or "disabled"),
        "consecutive_failures": int(row.get("consecutive_failures") or 0),
        "last_success_at": row.get("last_success_at"),
        "last_failure_at": row.get("last_failure_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def create_api_webhook(*, client_id: int, name: str, url: str, events: Sequence[str]) -> Dict[str, Any]:
    ensure_api_webhook_tables()
    normalized = validate_webhook_url(url, resolve_dns=True)
    event_list = normalize_webhook_events(events)
    clean_name = " ".join(str(name or "default").strip().split())[:80] or "default"
    webhook_id = f"wh_{secrets.token_hex(16)}"
    salt = secrets.token_hex(24)
    secret = derive_webhook_secret(int(client_id), webhook_id, salt)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT status FROM api_clients WHERE id=%s FOR UPDATE", (int(client_id),))
        client = _row_to_dict(cursor, cursor.fetchone())
        if not client or str(client.get("status") or "") != "active":
            raise WebhookError("client_not_active")
        cursor.execute("SELECT COUNT(*) FROM api_webhooks WHERE client_id=%s AND status='active'", (int(client_id),))
        count_row = cursor.fetchone()
        count = int(count_row[0] if not isinstance(count_row, dict) else next(iter(count_row.values())))
        if count >= webhook_max_per_client():
            raise WebhookError("webhook_limit_reached", limit=webhook_max_per_client())
        cursor.execute("SELECT 1 FROM api_webhooks WHERE client_id=%s AND url=%s AND status='active' LIMIT 1", (int(client_id), normalized["url"]))
        if cursor.fetchone():
            raise WebhookError("webhook_url_already_exists")
        cursor.execute(
            """
            INSERT INTO api_webhooks (
                client_id, webhook_id, name, url, secret_hash, secret_salt,
                secret_version, events, status, consecutive_failures
            ) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, 'active', 0)
            RETURNING *
            """,
            (
                int(client_id), webhook_id, clean_name, normalized["url"],
                hash_webhook_secret(secret), salt, ",".join(event_list),
            ),
        )
        row = _row_to_dict(cursor, cursor.fetchone()) or {}
        conn.commit()
        return {**_public_webhook(row), "signing_secret": secret, "secret_shown_once": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_api_webhooks(client_id: int) -> List[Dict[str, Any]]:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_webhooks WHERE client_id=%s ORDER BY id DESC", (int(client_id),))
        return [_public_webhook(row) for row in _rows_to_dicts(cursor, cursor.fetchall())]
    finally:
        cursor.close()
        conn.close()


def disable_api_webhook(client_id: int, webhook_id: str) -> bool:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_webhooks
            SET status='disabled', disabled_at=NOW(), updated_at=NOW()
            WHERE client_id=%s AND webhook_id=%s AND status<>'disabled'
            RETURNING id
            """,
            (int(client_id), str(webhook_id)),
        )
        row = cursor.fetchone()
        if row:
            internal_id = row.get("id") if isinstance(row, dict) else row[0]
            cursor.execute(
                """
                UPDATE api_webhook_deliveries
                SET status='failed', last_error='webhook_disabled', updated_at=NOW(), lease_until=NULL, locked_by=NULL
                WHERE webhook_id=%s AND status IN ('pending', 'retrying', 'delivering')
                """,
                (internal_id,),
            )
        conn.commit()
        return bool(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def rotate_api_webhook_secret(client_id: int, webhook_id: str) -> Dict[str, Any]:
    ensure_api_webhook_tables()
    salt = secrets.token_hex(24)
    secret = derive_webhook_secret(int(client_id), str(webhook_id), salt)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_webhooks
            SET secret_salt=%s, secret_hash=%s, secret_version=secret_version+1,
                consecutive_failures=0, updated_at=NOW()
            WHERE client_id=%s AND webhook_id=%s AND status='active'
            RETURNING *
            """,
            (salt, hash_webhook_secret(secret), int(client_id), str(webhook_id)),
        )
        row = _row_to_dict(cursor, cursor.fetchone())
        if not row:
            raise WebhookError("webhook_not_found")
        conn.commit()
        return {**_public_webhook(row), "signing_secret": secret, "secret_shown_once": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _public_delivery(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "delivery_id": str(row.get("delivery_id") or ""),
        "webhook_id": str(row.get("public_webhook_id") or row.get("webhook_public_id") or ""),
        "job_id": str(row.get("job_id") or ""),
        "event": str(row.get("event") or ""),
        "status": str(row.get("status") or ""),
        "attempt_count": int(row.get("attempt_count") or 0),
        "manual_retry_count": int(row.get("manual_retry_count") or 0),
        "response_status": row.get("response_status"),
        "last_error": row.get("last_error"),
        "delivered_at": row.get("delivered_at"),
        "next_attempt_at": row.get("next_attempt_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_webhook_deliveries(*, client_id: int, limit: int = 50, status: Optional[str] = None, webhook_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_api_webhook_tables()
    clauses = ["d.client_id=%s"]
    params: List[Any] = [int(client_id)]
    clean_status = str(status or "").strip().lower()
    if clean_status in {"pending", "retrying", "delivering", "succeeded", "failed"}:
        clauses.append("d.status=%s")
        params.append(clean_status)
    if webhook_id:
        clauses.append("w.webhook_id=%s")
        params.append(str(webhook_id))
    params.append(max(1, min(int(limit), 200)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT d.*, w.webhook_id AS public_webhook_id
            FROM api_webhook_deliveries d
            JOIN api_webhooks w ON w.id=d.webhook_id
            WHERE {' AND '.join(clauses)}
            ORDER BY d.created_at DESC
            LIMIT %s
            """,
            tuple(params),
        )
        return [_public_delivery(row) for row in _rows_to_dicts(cursor, cursor.fetchall())]
    finally:
        cursor.close()
        conn.close()


def get_webhook_delivery(client_id: int, delivery_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT d.*, w.webhook_id AS public_webhook_id
            FROM api_webhook_deliveries d
            JOIN api_webhooks w ON w.id=d.webhook_id
            WHERE d.client_id=%s AND d.delivery_id=%s
            LIMIT 1
            """,
            (int(client_id), str(delivery_id)),
        )
        row = _row_to_dict(cursor, cursor.fetchone())
        if not row:
            return None
        cursor.execute(
            """
            SELECT attempt_sequence, request_timestamp, resolved_ip, response_status,
                   success, duration_ms, error, response_body_snippet, created_at
            FROM api_webhook_delivery_attempts
            WHERE delivery_id=%s ORDER BY id DESC LIMIT 50
            """,
            (str(delivery_id),),
        )
        return {**_public_delivery(row), "attempts": _rows_to_dicts(cursor, cursor.fetchall())}
    finally:
        cursor.close()
        conn.close()


def retry_webhook_delivery(client_id: int, delivery_id: str) -> Dict[str, Any]:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_webhook_deliveries d
            SET status='retrying', attempt_count=0, manual_retry_count=manual_retry_count+1,
                next_attempt_at=NOW(), last_error=NULL, response_status=NULL,
                response_body_snippet=NULL, lease_until=NULL, locked_by=NULL, updated_at=NOW()
            FROM api_webhooks w
            WHERE d.webhook_id=w.id AND d.client_id=%s AND d.delivery_id=%s
              AND w.status='active' AND d.status IN ('failed', 'succeeded')
            RETURNING d.*, w.webhook_id AS public_webhook_id
            """,
            (int(client_id), str(delivery_id)),
        )
        row = _row_to_dict(cursor, cursor.fetchone())
        if not row:
            raise WebhookError("webhook_delivery_not_retryable")
        conn.commit()
        return _public_delivery(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def retry_delay_seconds(attempt_count: int) -> int:
    schedule = [0, 30, 120, 600, 1800, 7200, 21600, 43200]
    index = max(0, min(int(attempt_count), len(schedule) - 1))
    return schedule[index]


def touch_webhook_worker(worker_id: str, status: str, delivery_id: Optional[str] = None) -> None:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_worker_heartbeats (
                worker_id, worker_type, status, current_job_id, started_at, last_seen_at, metadata_json
            ) VALUES (%s, 'webhook', %s, %s, NOW(), NOW(), %s)
            ON CONFLICT (worker_id) DO UPDATE SET
                worker_type='webhook', status=EXCLUDED.status,
                current_job_id=EXCLUDED.current_job_id, last_seen_at=NOW(),
                metadata_json=EXCLUDED.metadata_json
            """,
            (
                str(worker_id)[:120], str(status)[:40], str(delivery_id or "")[:80] or None,
                json.dumps({"pid": os.getpid(), "hostname": socket.gethostname()}, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def recover_stale_webhook_deliveries() -> int:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_webhook_deliveries
            SET status='retrying', next_attempt_at=NOW(), locked_by=NULL, lease_until=NULL,
                last_error='delivery_worker_recovered', updated_at=NOW()
            WHERE status='delivering' AND (lease_until IS NULL OR lease_until < NOW())
            """
        )
        count = int(cursor.rowcount or 0)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def claim_next_webhook_delivery(worker_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT d.delivery_id
            FROM api_webhook_deliveries d
            JOIN api_webhooks w ON w.id=d.webhook_id
            WHERE d.status IN ('pending', 'retrying')
              AND d.next_attempt_at <= NOW()
              AND w.status='active'
            ORDER BY d.next_attempt_at ASC, d.created_at ASC
            FOR UPDATE OF d SKIP LOCKED
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return None
        delivery_id = row.get("delivery_id") if isinstance(row, dict) else row[0]
        cursor.execute(
            """
            UPDATE api_webhook_deliveries
            SET status='delivering', attempt_count=attempt_count+1, locked_by=%s,
                lease_until=NOW() + make_interval(secs => %s), updated_at=NOW()
            WHERE delivery_id=%s AND status IN ('pending', 'retrying')
            RETURNING *
            """,
            (str(worker_id)[:120], webhook_timeout_seconds() + 30, str(delivery_id)),
        )
        delivery = _row_to_dict(cursor, cursor.fetchone())
        if not delivery:
            conn.commit()
            return None
        cursor.execute("SELECT * FROM api_webhooks WHERE id=%s FOR UPDATE", (delivery.get("webhook_id"),))
        webhook = _row_to_dict(cursor, cursor.fetchone()) or {}
        conn.commit()
        return {**delivery, "webhook": webhook}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, resolved_ip: str, port: int, timeout: int):
        super().__init__(hostname, port=port, timeout=timeout, context=ssl.create_default_context())
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._resolved_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _send_signed_webhook(delivery: Dict[str, Any]) -> Dict[str, Any]:
    webhook = delivery.get("webhook") or {}
    target = validate_webhook_url(webhook.get("url"), resolve_dns=True)
    resolved_ip = target["addresses"][0]
    secret = derive_webhook_secret(
        int(webhook.get("client_id") or 0),
        str(webhook.get("webhook_id") or ""),
        str(webhook.get("secret_salt") or ""),
    )
    if not hmac.compare_digest(hash_webhook_secret(secret), str(webhook.get("secret_hash") or "")):
        raise WebhookError("webhook_secret_integrity_error")
    body = str(delivery.get("payload_json") or "{}").encode("utf-8")
    if len(body) > 256 * 1024:
        raise WebhookError("webhook_payload_too_large")
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "User-Agent": "DeepAlpha-Webhooks/1.0",
        "X-DeepAlpha-Event": str(delivery.get("event") or ""),
        "X-DeepAlpha-Delivery": str(delivery.get("delivery_id") or ""),
        "X-DeepAlpha-Timestamp": timestamp,
        "X-DeepAlpha-Signature": sign_webhook_payload(secret, timestamp, body),
    }
    started = time.monotonic()
    connection = _PinnedHTTPSConnection(target["host"], resolved_ip, target["port"], webhook_timeout_seconds())
    try:
        connection.request("POST", target["path"], body=body, headers=headers)
        response = connection.getresponse()
        snippet = response.read(4096).decode("utf-8", errors="replace")
        status = int(response.status)
        return {
            "success": 200 <= status < 300,
            "response_status": status,
            "response_body_snippet": snippet[:2000],
            "resolved_ip": resolved_ip,
            "request_timestamp": timestamp,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error": None if 200 <= status < 300 else f"webhook_http_{status}",
        }
    finally:
        connection.close()


def _record_delivery_result(delivery: Dict[str, Any], result: Dict[str, Any]) -> None:
    delivery_id = str(delivery.get("delivery_id") or "")
    webhook = delivery.get("webhook") or {}
    internal_webhook_id = int(webhook.get("id") or 0)
    success = bool(result.get("success"))
    current_attempt = int(delivery.get("attempt_count") or 0)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COALESCE(MAX(attempt_sequence), 0) + 1 FROM api_webhook_delivery_attempts WHERE delivery_id=%s", (delivery_id,))
        seq_row = cursor.fetchone()
        sequence = int(seq_row[0] if not isinstance(seq_row, dict) else next(iter(seq_row.values())))
        cursor.execute(
            """
            INSERT INTO api_webhook_delivery_attempts (
                delivery_id, attempt_sequence, request_timestamp, resolved_ip,
                response_status, success, duration_ms, error, response_body_snippet
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                delivery_id, sequence, str(result.get("request_timestamp") or int(time.time())),
                str(result.get("resolved_ip") or "")[:100] or None,
                result.get("response_status"), success, int(result.get("duration_ms") or 0),
                str(result.get("error") or "")[:500] or None,
                str(result.get("response_body_snippet") or "")[:2000] or None,
            ),
        )
        if success:
            cursor.execute(
                """
                UPDATE api_webhook_deliveries
                SET status='succeeded', response_status=%s, response_body_snippet=%s,
                    last_error=NULL, delivered_at=NOW(), locked_by=NULL, lease_until=NULL,
                    updated_at=NOW()
                WHERE delivery_id=%s
                """,
                (result.get("response_status"), str(result.get("response_body_snippet") or "")[:2000], delivery_id),
            )
            cursor.execute(
                """
                UPDATE api_webhooks
                SET consecutive_failures=0, last_success_at=NOW(), updated_at=NOW()
                WHERE id=%s
                """,
                (internal_webhook_id,),
            )
        else:
            cursor.execute("SELECT consecutive_failures FROM api_webhooks WHERE id=%s FOR UPDATE", (internal_webhook_id,))
            failure_row = cursor.fetchone()
            failures = int((failure_row[0] if not isinstance(failure_row, dict) else next(iter(failure_row.values()))) or 0) + 1
            terminal = current_attempt >= webhook_max_attempts()
            disable = failures >= webhook_disable_after_failures()
            next_delay = retry_delay_seconds(current_attempt)
            cursor.execute(
                """
                UPDATE api_webhook_deliveries
                SET status=%s,
                    next_attempt_at=CASE WHEN %s THEN next_attempt_at ELSE NOW() + make_interval(secs => %s) END,
                    response_status=%s, response_body_snippet=%s, last_error=%s,
                    locked_by=NULL, lease_until=NULL, updated_at=NOW()
                WHERE delivery_id=%s
                """,
                (
                    "failed" if terminal or disable else "retrying",
                    terminal or disable,
                    next_delay,
                    result.get("response_status"),
                    str(result.get("response_body_snippet") or "")[:2000],
                    str(result.get("error") or "webhook_delivery_failed")[:500],
                    delivery_id,
                ),
            )
            cursor.execute(
                """
                UPDATE api_webhooks
                SET consecutive_failures=%s, last_failure_at=NOW(),
                    status=CASE WHEN %s THEN 'disabled' ELSE status END,
                    disabled_at=CASE WHEN %s THEN NOW() ELSE disabled_at END,
                    updated_at=NOW()
                WHERE id=%s
                """,
                (failures, disable, disable, internal_webhook_id),
            )
            if disable:
                cursor.execute(
                    """
                    UPDATE api_webhook_deliveries
                    SET status='failed', last_error='webhook_auto_disabled', locked_by=NULL,
                        lease_until=NULL, updated_at=NOW()
                    WHERE webhook_id=%s AND status IN ('pending', 'retrying', 'delivering')
                    """,
                    (internal_webhook_id,),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def process_webhook_delivery(delivery: Dict[str, Any]) -> None:
    try:
        result = _send_signed_webhook(delivery)
    except WebhookError as exc:
        result = {
            "success": False,
            "response_status": None,
            "response_body_snippet": "",
            "resolved_ip": "",
            "request_timestamp": str(int(time.time())),
            "duration_ms": 0,
            "error": exc.code,
        }
    except Exception as exc:
        logger.exception("WEBHOOK_DELIVERY_REQUEST_FAILED delivery_id=%s", delivery.get("delivery_id"))
        result = {
            "success": False,
            "response_status": None,
            "response_body_snippet": "",
            "resolved_ip": "",
            "request_timestamp": str(int(time.time())),
            "duration_ms": 0,
            "error": f"webhook_transport_{type(exc).__name__.lower()}",
        }
    _record_delivery_result(delivery, result)


def get_webhook_runtime_health(*, include_workers: bool = False) -> Dict[str, Any]:
    ensure_api_webhook_tables()
    stale = webhook_worker_stale_seconds()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM api_webhooks WHERE status='active') AS active_webhooks,
              COUNT(*) FILTER (WHERE status IN ('pending','retrying')) AS queued,
              COUNT(*) FILTER (WHERE status='delivering') AS delivering,
              COUNT(*) FILTER (WHERE status='failed' AND updated_at >= NOW() - INTERVAL '24 hours') AS failed_24h,
              COUNT(*) FILTER (WHERE status='succeeded' AND delivered_at >= NOW() - INTERVAL '24 hours') AS succeeded_24h,
              EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status IN ('pending','retrying')))) AS oldest_queued_age_seconds
            FROM api_webhook_deliveries
            """
        )
        metrics = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            SELECT worker_id, status, current_job_id AS current_delivery_id,
                   started_at, last_seen_at,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS heartbeat_age_seconds,
                   (last_seen_at >= NOW() - make_interval(secs => %s)) AS fresh
            FROM api_worker_heartbeats
            WHERE worker_type='webhook'
            ORDER BY last_seen_at DESC LIMIT 20
            """,
            (stale,),
        )
        workers = _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()
    active = int(metrics.get("active_webhooks") or 0)
    queued = int(metrics.get("queued") or 0)
    oldest = round(float(metrics.get("oldest_queued_age_seconds") or 0), 1)
    fresh = sum(1 for worker in workers if bool(worker.get("fresh")))
    warnings: List[str] = []
    if active and fresh == 0:
        warnings.append("no_fresh_webhook_worker")
    if queued >= webhook_queue_warning_size():
        warnings.append("webhook_queue_size_high")
    if oldest >= webhook_queue_warning_age_seconds():
        warnings.append("webhook_queue_wait_high")
    result: Dict[str, Any] = {
        "status": "operational" if not warnings else "degraded",
        "active_webhooks": active,
        "worker_available": fresh > 0,
        "fresh_workers": fresh,
        "queue": {
            "queued": queued,
            "delivering": int(metrics.get("delivering") or 0),
            "oldest_queued_age_seconds": oldest,
        },
        "recent": {
            "succeeded_24h": int(metrics.get("succeeded_24h") or 0),
            "failed_24h": int(metrics.get("failed_24h") or 0),
        },
        "warnings": warnings,
    }
    if include_workers:
        result["workers"] = workers
    return result
