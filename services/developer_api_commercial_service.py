import hashlib
import json
import logging
import os
import secrets
import socket
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from db.database import acquire_distributed_lock, get_connection, release_distributed_lock
from services.developer_api_billing_service import ApiBillingError, normalize_idempotency_key
from services.developer_api_observability_service import ensure_api_observability_tables
from services.developer_api_service import generate_api_key
from services.developer_portal_service import (
    DeveloperPortalError,
    _clean_key_name,
    _owned_client,
    max_keys_per_project,
    normalize_self_service_scopes,
)
from services.ton_chain_service import normalize_ton_address
from services.treasury_service import (
    build_ton_text_comment_payload_boc,
    decode_ton_text_comment_from_msg,
    get_public_treasury_address,
    incoming_enabled,
    ton_network_id,
)

logger = logging.getLogger(__name__)

_COMMERCIAL_TABLES_READY = False


class ApiCommercialError(ValueError):
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


def _env_true(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "enabled"}


def commercial_launch_enabled() -> bool:
    return _env_true("API_COMMERCIAL_LAUNCH_ENABLED", False)


def live_keys_globally_enabled() -> bool:
    return commercial_launch_enabled() and _env_true("API_LIVE_KEYS_ENABLED", False)


def live_auto_approve_on_payment() -> bool:
    return live_keys_globally_enabled() and _env_true("API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT", False)


def invoice_ttl_minutes() -> int:
    return _safe_int("API_CREDIT_INVOICE_TTL_MINUTES", 60, 10, 1440)


def confirmation_seconds() -> int:
    return _safe_int("API_CREDIT_CONFIRMATION_SECONDS", 20, 0, 3600)


def commercial_poll_seconds() -> float:
    try:
        value = float(str(os.getenv("API_COMMERCIAL_POLL_SECONDS", "10") or "10"))
    except Exception:
        value = 10.0
    return max(2.0, min(value, 300.0))


def commercial_worker_stale_seconds() -> int:
    return _safe_int("API_COMMERCIAL_WORKER_STALE_SECONDS", 90, 20, 1800)


def _runtime_network() -> str:
    value = str(os.getenv("TON_NETWORK", "mainnet") or "mainnet").strip().lower()
    return "testnet" if "test" in value else "mainnet"


def _canonical_fingerprint(payload: Any) -> str:
    text = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_package_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    if not code or len(code) > 64 or not code[0].isalpha() or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_" for ch in code):
        raise ApiCommercialError("invalid_package_code")
    return code


def _clean_package_name(value: Any) -> str:
    name = " ".join(str(value or "").strip().split())[:120]
    if len(name) < 2:
        raise ApiCommercialError("package_name_required")
    return name


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _seed_packages(cursor) -> None:
    raw = str(os.getenv("API_CREDIT_PACKAGES_JSON", "") or "").strip()
    if not raw:
        return
    try:
        packages = json.loads(raw)
    except Exception:
        logger.error("API_CREDIT_PACKAGES_JSON_INVALID")
        return
    if not isinstance(packages, list):
        return
    for index, item in enumerate(packages[:50]):
        if not isinstance(item, dict):
            continue
        try:
            code = _clean_package_code(item.get("package_code") or item.get("code"))
            name = _clean_package_name(item.get("display_name") or item.get("name") or code)
            credits = int(item.get("credits") or 0)
            price_nano = int(item.get("price_nano") or 0)
            if credits <= 0 or credits > 1_000_000_000 or price_nano <= 0:
                continue
            cursor.execute(
                """
                INSERT INTO api_credit_packages (
                    package_code, display_name, credits, price_nano, enabled, sort_order, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (package_code) DO NOTHING
                """,
                (
                    code,
                    name,
                    credits,
                    price_nano,
                    bool(item.get("enabled", True)),
                    int(item.get("sort_order") or index),
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                ),
            )
        except Exception:
            logger.warning("API_CREDIT_PACKAGE_SEED_SKIPPED index=%s", index)


def ensure_api_commercial_tables() -> None:
    global _COMMERCIAL_TABLES_READY
    if _COMMERCIAL_TABLES_READY:
        return
    ensure_api_observability_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS commercial_status TEXT NOT NULL DEFAULT 'test_only'")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS live_keys_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS monthly_spend_limit_credits INTEGER NOT NULL DEFAULT 0")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS low_balance_threshold INTEGER NOT NULL DEFAULT 20")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_credit_packages (
                id BIGSERIAL PRIMARY KEY,
                package_code TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                credits INTEGER NOT NULL,
                price_nano BIGINT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                CHECK (credits > 0),
                CHECK (price_nano > 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_credit_invoices (
                id BIGSERIAL PRIMARY KEY,
                invoice_id TEXT NOT NULL UNIQUE,
                public_reference TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL,
                client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                package_code TEXT NOT NULL,
                package_name TEXT NOT NULL,
                credits INTEGER NOT NULL,
                price_nano BIGINT NOT NULL,
                treasury_wallet_id BIGINT NOT NULL,
                treasury_address TEXT NOT NULL,
                network TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                idempotency_key TEXT NOT NULL,
                request_fingerprint TEXT NOT NULL,
                tx_hash TEXT UNIQUE,
                source_address TEXT,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                paid_at TIMESTAMP,
                cancelled_at TIMESTAMP,
                last_checked_at TIMESTAMP,
                last_error TEXT,
                metadata_json TEXT,
                UNIQUE(user_id, idempotency_key),
                CHECK (credits > 0),
                CHECK (price_nano > 0)
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_credit_invoices_client_created ON api_credit_invoices(client_id, created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_credit_invoices_status ON api_credit_invoices(status, expires_at)")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_live_access_requests (
                id BIGSERIAL PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL,
                client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                use_case TEXT NOT NULL,
                expected_monthly_requests INTEGER NOT NULL DEFAULT 0,
                terms_version TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                reviewed_at TIMESTAMP,
                reviewed_by TEXT,
                review_note TEXT
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_live_requests_status ON api_live_access_requests(status, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_live_requests_client ON api_live_access_requests(client_id, created_at DESC)")
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION enforce_api_monthly_spend_limit()
            RETURNS trigger AS $$
            DECLARE
                v_limit INTEGER;
                v_used BIGINT;
            BEGIN
                SELECT monthly_spend_limit_credits INTO v_limit
                FROM api_clients WHERE id=NEW.client_id FOR UPDATE;
                IF COALESCE(v_limit, 0) <= 0 THEN
                    RETURN NEW;
                END IF;
                SELECT COALESCE(SUM(units), 0) INTO v_used
                FROM api_credit_reservations
                WHERE client_id=NEW.client_id
                  AND status IN ('reserved', 'charged')
                  AND created_at >= date_trunc('month', NOW());
                IF v_used + NEW.units > v_limit THEN
                    RAISE EXCEPTION 'monthly_spend_limit_exceeded:%:%:%', v_limit, v_used, NEW.units;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        cursor.execute("DROP TRIGGER IF EXISTS trg_enforce_api_monthly_spend_limit ON api_credit_reservations")
        cursor.execute(
            """
            CREATE TRIGGER trg_enforce_api_monthly_spend_limit
            BEFORE INSERT ON api_credit_reservations
            FOR EACH ROW EXECUTE FUNCTION enforce_api_monthly_spend_limit()
            """
        )
        _seed_packages(cursor)
        conn.commit()
        _COMMERCIAL_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_credit_packages(*, include_disabled: bool = False) -> List[Dict[str, Any]]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        where = "" if include_disabled else "WHERE enabled=TRUE"
        cursor.execute(f"SELECT * FROM api_credit_packages {where} ORDER BY sort_order, credits, id")
        rows = _rows_to_dicts(cursor, cursor.fetchall())
        for item in rows:
            item["price_ton"] = str((Decimal(int(item.get("price_nano") or 0)) / Decimal(1_000_000_000)).normalize())
            item["metadata"] = _parse_json_object(item.pop("metadata_json", "{}"))
        return rows
    finally:
        cursor.close()
        conn.close()


def upsert_credit_package(
    *,
    package_code: str,
    display_name: str,
    credits: int,
    price_nano: int,
    enabled: bool,
    sort_order: int = 0,
    actor: str = "admin",
) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    code = _clean_package_code(package_code)
    name = _clean_package_name(display_name)
    units = int(credits)
    price = int(price_nano)
    if units <= 0 or units > 1_000_000_000:
        raise ApiCommercialError("invalid_package_credits")
    if price <= 0 or price > 10_000_000_000_000:
        raise ApiCommercialError("invalid_package_price")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_credit_packages (
                package_code, display_name, credits, price_nano, enabled, sort_order
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (package_code) DO UPDATE SET
                display_name=EXCLUDED.display_name,
                credits=EXCLUDED.credits,
                price_nano=EXCLUDED.price_nano,
                enabled=EXCLUDED.enabled,
                sort_order=EXCLUDED.sort_order,
                updated_at=NOW()
            RETURNING *
            """,
            (code, name, units, price, bool(enabled), int(sort_order)),
        )
        package = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'commercial.package.upsert', 'api_credit_package', %s, %s)
            """,
            (str(actor)[:100], code, json.dumps({"credits": units, "price_nano": price, "enabled": bool(enabled)})),
        )
        conn.commit()
        return package
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def monthly_spend_snapshot(client_id: int, cursor=None) -> Dict[str, int]:
    ensure_api_commercial_tables()
    own = cursor is None
    conn = get_connection() if own else None
    cur = conn.cursor() if own else cursor
    try:
        cur.execute(
            """
            SELECT monthly_spend_limit_credits, low_balance_threshold, credit_balance
            FROM api_clients WHERE id=%s
            """,
            (int(client_id),),
        )
        client = _row_to_dict(cur, cur.fetchone()) or {}
        cur.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM api_credit_reservations
            WHERE client_id=%s AND status IN ('reserved','charged')
              AND created_at >= date_trunc('month', NOW())
            """,
            (int(client_id),),
        )
        row = cur.fetchone()
        used = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        limit = int(client.get("monthly_spend_limit_credits") or 0)
        balance = int(client.get("credit_balance") or 0)
        threshold = int(client.get("low_balance_threshold") or 0)
        return {
            "used": used,
            "limit": limit,
            "remaining": max(0, limit - used) if limit > 0 else 0,
            "balance": balance,
            "low_balance_threshold": threshold,
            "low_balance": threshold > 0 and balance <= threshold,
        }
    finally:
        if own and conn:
            cur.close()
            conn.close()


def set_project_commercial_settings(
    *,
    user_id: int,
    client_id: int,
    monthly_spend_limit_credits: int,
    low_balance_threshold: int,
) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    limit = int(monthly_spend_limit_credits)
    threshold = int(low_balance_threshold)
    if limit < 0 or limit > 1_000_000_000:
        raise ApiCommercialError("invalid_monthly_spend_limit")
    if threshold < 0 or threshold > 1_000_000_000:
        raise ApiCommercialError("invalid_low_balance_threshold")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise ApiCommercialError("project_not_found")
        cursor.execute(
            """
            UPDATE api_clients
            SET monthly_spend_limit_credits=%s, low_balance_threshold=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
            """,
            (limit, threshold, int(client_id)),
        )
        updated = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'commercial.settings.update', 'api_client', %s, %s)
            """,
            (
                f"user:{int(user_id)}",
                str(client_id),
                json.dumps({"monthly_spend_limit_credits": limit, "low_balance_threshold": threshold}),
            ),
        )
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _invoice_public(row: Dict[str, Any]) -> Dict[str, Any]:
    price_nano = int(row.get("price_nano") or 0)
    reference = str(row.get("public_reference") or "")
    address = str(row.get("treasury_address") or "")
    result = {
        "invoice_id": str(row.get("invoice_id") or ""),
        "client_id": int(row.get("client_id") or 0),
        "package_code": str(row.get("package_code") or ""),
        "package_name": str(row.get("package_name") or ""),
        "credits": int(row.get("credits") or 0),
        "price_nano": price_nano,
        "price_ton": str((Decimal(price_nano) / Decimal(1_000_000_000)).normalize()),
        "treasury_address": address,
        "network": str(row.get("network") or ""),
        "public_reference": reference,
        "status": str(row.get("status") or "pending"),
        "tx_hash": row.get("tx_hash"),
        "source_address": row.get("source_address"),
        "expires_at": row.get("expires_at"),
        "created_at": row.get("created_at"),
        "paid_at": row.get("paid_at"),
        "last_checked_at": row.get("last_checked_at"),
        "last_error": row.get("last_error"),
    }
    if address and reference and price_nano > 0:
        result["ton_transfer_url"] = f"ton://transfer/{quote(address, safe='')}?amount={price_nano}&text={quote(reference, safe='')}"
        try:
            result["payload_boc"] = build_ton_text_comment_payload_boc(reference)
        except Exception:
            result["payload_boc"] = None
        result["network_id"] = ton_network_id(result["network"])
    return result


def create_credit_invoice(
    *,
    user_id: int,
    client_id: int,
    package_code: str,
    idempotency_key: str,
) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    if not commercial_launch_enabled():
        raise ApiCommercialError("commercial_launch_disabled")
    if not incoming_enabled():
        raise ApiCommercialError("treasury_incoming_disabled")
    key = normalize_idempotency_key(idempotency_key)
    code = _clean_package_code(package_code)
    treasury = get_public_treasury_address()
    if not treasury.get("ok"):
        raise ApiCommercialError(str(treasury.get("error") or "treasury_not_configured"))
    network = _runtime_network()
    fingerprint = _canonical_fingerprint({"client_id": int(client_id), "package_code": code})
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise ApiCommercialError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise ApiCommercialError("project_not_active")
        cursor.execute(
            "SELECT * FROM api_credit_invoices WHERE user_id=%s AND idempotency_key=%s FOR UPDATE",
            (int(user_id), key),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if existing:
            if str(existing.get("request_fingerprint") or "") != fingerprint:
                raise ApiCommercialError("idempotency_conflict")
            conn.commit()
            return {**_invoice_public(existing), "idempotent": True}
        cursor.execute("SELECT * FROM api_credit_packages WHERE package_code=%s AND enabled=TRUE FOR UPDATE", (code,))
        package = _row_to_dict(cursor, cursor.fetchone())
        if not package:
            raise ApiCommercialError("credit_package_not_found")
        invoice_id = f"inv_{secrets.token_hex(16)}"
        reference = f"api_pay_{secrets.token_hex(16)}"
        cursor.execute(
            """
            INSERT INTO api_credit_invoices (
                invoice_id, public_reference, user_id, client_id, package_code,
                package_name, credits, price_nano, treasury_wallet_id,
                treasury_address, network, status, idempotency_key,
                request_fingerprint, expires_at, metadata_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'pending', %s, %s, NOW() + make_interval(mins => %s), %s
            ) RETURNING *
            """,
            (
                invoice_id,
                reference,
                int(user_id),
                int(client_id),
                code,
                str(package.get("display_name") or code),
                int(package.get("credits") or 0),
                int(package.get("price_nano") or 0),
                int(treasury.get("wallet_id") or 0),
                str(treasury.get("address") or ""),
                network,
                key,
                fingerprint,
                invoice_ttl_minutes(),
                json.dumps({"package_id": package.get("id")}, ensure_ascii=False),
            ),
        )
        invoice = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'commercial.invoice.create', 'api_credit_invoice', %s, %s)
            """,
            (
                f"user:{int(user_id)}",
                invoice_id,
                json.dumps({"client_id": int(client_id), "package_code": code, "credits": invoice.get("credits"), "price_nano": invoice.get("price_nano")}),
            ),
        )
        conn.commit()
        return {**_invoice_public(invoice), "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_owned_credit_invoices(*, user_id: int, client_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_api_commercial_tables()
    params: List[Any] = [int(user_id)]
    client_clause = ""
    if client_id is not None:
        client_clause = " AND i.client_id=%s"
        params.append(int(client_id))
    params.append(max(1, min(int(limit), 200)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT i.*
            FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE o.user_id=%s {client_clause}
            ORDER BY i.id DESC LIMIT %s
            """,
            tuple(params),
        )
        return [_invoice_public(item) for item in _rows_to_dicts(cursor, cursor.fetchall())]
    finally:
        cursor.close()
        conn.close()


def get_owned_credit_invoice(*, user_id: int, invoice_id: str, for_update: bool = False) -> Optional[Dict[str, Any]]:
    ensure_api_commercial_tables()
    suffix = " FOR UPDATE OF i" if for_update else ""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT i.* FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE o.user_id=%s AND i.invoice_id=%s {suffix}
            """,
            (int(user_id), str(invoice_id)),
        )
        row = _row_to_dict(cursor, cursor.fetchone())
        return _invoice_public(row) if row else None
    finally:
        cursor.close()
        conn.close()


def cancel_credit_invoice(*, user_id: int, invoice_id: str) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_credit_invoices i
            SET status='cancelled', cancelled_at=NOW(), updated_at=NOW()
            FROM api_client_owners o
            WHERE i.client_id=o.client_id AND o.user_id=%s AND i.invoice_id=%s
              AND i.status='pending'
            RETURNING i.*
            """,
            (int(user_id), str(invoice_id)),
        )
        row = _row_to_dict(cursor, cursor.fetchone())
        if not row:
            raise ApiCommercialError("invoice_not_cancellable")
        conn.commit()
        return _invoice_public(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def request_live_access(
    *,
    user_id: int,
    client_id: int,
    use_case: str,
    expected_monthly_requests: int,
    terms_accepted: bool,
    terms_version: str = "2026-07",
) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    if not commercial_launch_enabled():
        raise ApiCommercialError("commercial_launch_disabled")
    if not terms_accepted:
        raise ApiCommercialError("commercial_terms_required")
    clean_use_case = " ".join(str(use_case or "").strip().split())[:1000]
    if len(clean_use_case) < 20:
        raise ApiCommercialError("live_use_case_required")
    volume = max(0, min(int(expected_monthly_requests), 100_000_000))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise ApiCommercialError("project_not_found")
        if bool(client.get("live_keys_enabled")):
            raise ApiCommercialError("live_access_already_enabled")
        cursor.execute(
            "SELECT * FROM api_live_access_requests WHERE client_id=%s AND status='pending' ORDER BY id DESC LIMIT 1 FOR UPDATE",
            (int(client_id),),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if existing:
            conn.commit()
            return {**existing, "idempotent": True}
        request_id = f"live_req_{secrets.token_hex(16)}"
        cursor.execute(
            """
            INSERT INTO api_live_access_requests (
                request_id, user_id, client_id, status, use_case,
                expected_monthly_requests, terms_version
            ) VALUES (%s, %s, %s, 'pending', %s, %s, %s)
            RETURNING *
            """,
            (request_id, int(user_id), int(client_id), clean_use_case, volume, str(terms_version)[:40]),
        )
        request_row = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute("UPDATE api_clients SET commercial_status='live_review', updated_at=NOW() WHERE id=%s", (int(client_id),))
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'commercial.live.request', 'api_client', %s, %s)
            """,
            (f"user:{int(user_id)}", str(client_id), json.dumps({"request_id": request_id, "expected_monthly_requests": volume})),
        )
        conn.commit()
        return {**request_row, "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def review_live_access(*, client_id: int, approved: bool, actor: str, note: str = "") -> Dict[str, Any]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (int(client_id),))
        client = _row_to_dict(cursor, cursor.fetchone())
        if not client:
            raise ApiCommercialError("project_not_found")
        cursor.execute(
            "SELECT * FROM api_live_access_requests WHERE client_id=%s AND status='pending' ORDER BY id DESC LIMIT 1 FOR UPDATE",
            (int(client_id),),
        )
        request_row = _row_to_dict(cursor, cursor.fetchone())
        if not request_row:
            raise ApiCommercialError("live_access_request_not_found")
        next_status = "approved" if approved else "rejected"
        cursor.execute(
            """
            UPDATE api_live_access_requests
            SET status=%s, reviewed_at=NOW(), reviewed_by=%s, review_note=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
            """,
            (next_status, str(actor)[:100], str(note or "")[:1000], int(request_row.get("id") or 0)),
        )
        reviewed = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            UPDATE api_clients
            SET live_keys_enabled=%s, commercial_status=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
            """,
            (bool(approved), "live_enabled" if approved else "test_only", int(client_id)),
        )
        updated_client = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, %s, 'api_client', %s, %s)
            """,
            (
                str(actor)[:100],
                "commercial.live.approve" if approved else "commercial.live.reject",
                str(client_id),
                json.dumps({"request_id": reviewed.get("request_id"), "note": str(note or "")[:1000]}),
            ),
        )
        conn.commit()
        return {"request": reviewed, "client": updated_client}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def issue_user_live_api_key(
    *,
    user_id: int,
    client_id: int,
    name: str,
    scopes: Optional[Iterable[str]],
) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    if not live_keys_globally_enabled():
        raise ApiCommercialError("live_keys_disabled")
    scope_list = normalize_self_service_scopes(scopes)
    raw_key, key_prefix, key_hash = generate_api_key("live")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise ApiCommercialError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise ApiCommercialError("project_not_active")
        if not bool(client.get("live_keys_enabled")) or str(client.get("commercial_status") or "") != "live_enabled":
            raise ApiCommercialError("live_access_not_approved")
        cursor.execute("SELECT COUNT(*) FROM api_keys WHERE client_id=%s AND status='active'", (int(client_id),))
        row = cursor.fetchone()
        count = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        if count >= max_keys_per_project():
            raise ApiCommercialError("key_limit_reached", limit=max_keys_per_project())
        cursor.execute(
            """
            INSERT INTO api_keys (client_id, name, environment, key_hash, key_prefix, scopes, status)
            VALUES (%s, %s, 'live', %s, %s, %s, 'active')
            RETURNING id, client_id, name, environment, key_prefix, scopes, status, created_at
            """,
            (int(client_id), _clean_key_name(name), key_hash, key_prefix, ",".join(scope_list)),
        )
        key = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'commercial.live_key.issue', 'api_key', %s, %s)
            """,
            (f"user:{int(user_id)}", str(key.get("id") or ""), json.dumps({"client_id": int(client_id), "scopes": scope_list})),
        )
        conn.commit()
        return {**key, "raw_key": raw_key, "scopes": scope_list}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def rotate_user_api_key_preserving_environment(*, user_id: int, key_id: int) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT k.* FROM api_keys k
            JOIN api_client_owners o ON o.client_id=k.client_id
            WHERE k.id=%s AND o.user_id=%s FOR UPDATE OF k
            """,
            (int(key_id), int(user_id)),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if not existing:
            raise DeveloperPortalError("key_not_found")
        if str(existing.get("status") or "") != "active":
            raise DeveloperPortalError("key_not_active")
        environment = "live" if str(existing.get("environment") or "") == "live" else "test"
        if environment == "live" and not live_keys_globally_enabled():
            raise ApiCommercialError("live_keys_disabled")
        raw_key, key_prefix, key_hash = generate_api_key(environment)
        cursor.execute("UPDATE api_keys SET status='revoked', revoked_at=NOW() WHERE id=%s", (int(key_id),))
        cursor.execute(
            """
            INSERT INTO api_keys (client_id, name, environment, key_hash, key_prefix, scopes, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'active')
            RETURNING id, client_id, name, environment, key_prefix, scopes, status, created_at
            """,
            (
                int(existing.get("client_id") or 0),
                _clean_key_name(existing.get("name")),
                environment,
                key_hash,
                key_prefix,
                str(existing.get("scopes") or ""),
            ),
        )
        replacement = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'portal.key.rotate', 'api_key', %s, %s)
            """,
            (
                f"user:{int(user_id)}",
                str(replacement.get("id") or ""),
                json.dumps({"client_id": replacement.get("client_id"), "replaced_key_id": int(key_id), "environment": environment}),
            ),
        )
        conn.commit()
        return {
            **replacement,
            "raw_key": raw_key,
            "scopes": [scope for scope in str(replacement.get("scopes") or "").split(",") if scope],
            "replaced_key_id": int(key_id),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _transaction_hash(tx: Dict[str, Any]) -> str:
    transaction_id = tx.get("transaction_id") if isinstance(tx.get("transaction_id"), dict) else {}
    return str(transaction_id.get("hash") or tx.get("hash") or "").strip()


def _transaction_lt(tx: Dict[str, Any]) -> str:
    transaction_id = tx.get("transaction_id") if isinstance(tx.get("transaction_id"), dict) else {}
    return str(transaction_id.get("lt") or tx.get("lt") or "").strip()


def _transaction_success(tx: Dict[str, Any]) -> bool:
    if tx.get("aborted") is True:
        return False
    description = tx.get("description") if isinstance(tx.get("description"), dict) else {}
    if description.get("aborted") is True:
        return False
    for root in (tx, description):
        for key in ("compute_phase", "compute_ph", "action_phase", "action"):
            phase = root.get(key) if isinstance(root, dict) else None
            if not isinstance(phase, dict):
                continue
            if phase.get("success") is False:
                return False
            if str(phase.get("exit_code", "0")) not in {"", "0", "None"}:
                return False
            if str(phase.get("result_code", "0")) not in {"", "0", "None"}:
                return False
    return True


def _invoice_reference_from_tx(tx: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    incoming = tx.get("in_msg") if isinstance(tx.get("in_msg"), dict) else {}
    comment = decode_ton_text_comment_from_msg(incoming)
    reference = ""
    for token in str(comment or "").replace("\n", " ").split():
        if token.startswith("api_pay_"):
            reference = token.strip()
            break
    return reference, incoming


def _settle_invoice_from_tx(invoice: Dict[str, Any], tx: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    invoice_id = str(invoice.get("invoice_id") or "")
    tx_hash = _transaction_hash(tx)
    if not invoice_id or not tx_hash:
        return {"ok": False, "error": "transaction_identity_missing"}
    source = normalize_ton_address(str(incoming.get("source") or incoming.get("src") or ""))
    destination = normalize_ton_address(str(incoming.get("destination") or incoming.get("dest") or ""))
    expected_destination = normalize_ton_address(str(invoice.get("treasury_address") or ""))
    try:
        amount_nano = int(incoming.get("value") or incoming.get("amount") or 0)
    except Exception:
        amount_nano = 0
    tx_time = int(tx.get("utime") or tx.get("timestamp") or 0)
    created = invoice.get("created_at")
    expires = invoice.get("expires_at")
    if not _transaction_success(tx):
        return {"ok": False, "error": "transaction_failed"}
    if destination != expected_destination:
        return {"ok": False, "error": "destination_mismatch"}
    if amount_nano != int(invoice.get("price_nano") or 0):
        return {"ok": False, "error": "amount_mismatch"}
    if str(invoice.get("network") or "") != _runtime_network():
        return {"ok": False, "error": "network_mismatch"}
    if tx_time and int(time.time()) - tx_time < confirmation_seconds():
        return {"ok": False, "error": "awaiting_confirmation", "retryable": True}
    if tx_time and created:
        created_ts = int(created.replace(tzinfo=timezone.utc).timestamp()) if getattr(created, "tzinfo", None) is None else int(created.timestamp())
        if tx_time < created_ts - 120:
            return {"ok": False, "error": "transaction_before_invoice"}
    if tx_time and expires:
        expires_ts = int(expires.replace(tzinfo=timezone.utc).timestamp()) if getattr(expires, "tzinfo", None) is None else int(expires.timestamp())
        if tx_time > expires_ts:
            return {"ok": False, "error": "invoice_expired_before_payment"}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE", (invoice_id,))
        locked = _row_to_dict(cursor, cursor.fetchone())
        if not locked:
            raise ApiCommercialError("invoice_not_found")
        if str(locked.get("status") or "") == "paid":
            conn.commit()
            return {"ok": True, "idempotent": True, "invoice": _invoice_public(locked)}
        if str(locked.get("status") or "") not in {"pending", "expired"}:
            raise ApiCommercialError("invoice_not_payable")
        cursor.execute("SELECT id FROM api_credit_invoices WHERE tx_hash=%s AND invoice_id<>%s", (tx_hash, invoice_id))
        if cursor.fetchone():
            raise ApiCommercialError("tx_hash_not_unique")
        client_id = int(locked.get("client_id") or 0)
        cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (client_id,))
        client = _row_to_dict(cursor, cursor.fetchone())
        if not client or str(client.get("status") or "") != "active":
            raise ApiCommercialError("client_not_active")
        ledger_key = f"invoice:{invoice_id}"
        cursor.execute("SELECT * FROM api_credit_ledger WHERE client_id=%s AND idempotency_key=%s", (client_id, ledger_key))
        existing_ledger = _row_to_dict(cursor, cursor.fetchone())
        current_balance = int(client.get("credit_balance") or 0)
        credits = int(locked.get("credits") or 0)
        if existing_ledger:
            next_balance = int(existing_ledger.get("balance_after") or current_balance)
        else:
            next_balance = current_balance + credits
            cursor.execute("UPDATE api_clients SET credit_balance=%s, updated_at=NOW() WHERE id=%s", (next_balance, client_id))
            cursor.execute(
                """
                INSERT INTO api_credit_ledger (
                    client_id, event_type, amount, balance_after, idempotency_key, metadata_json
                ) VALUES (%s, 'purchase', %s, %s, %s, %s)
                """,
                (
                    client_id,
                    credits,
                    next_balance,
                    ledger_key,
                    json.dumps({"invoice_id": invoice_id, "package_code": locked.get("package_code"), "tx_hash": tx_hash, "price_nano": locked.get("price_nano")}),
                ),
            )
        cursor.execute(
            """
            UPDATE api_credit_invoices
            SET status='paid', tx_hash=%s, source_address=%s, paid_at=NOW(),
                updated_at=NOW(), last_checked_at=NOW(), last_error=NULL
            WHERE invoice_id=%s RETURNING *
            """,
            (tx_hash, source, invoice_id),
        )
        paid = _row_to_dict(cursor, cursor.fetchone()) or {}
        if live_auto_approve_on_payment() and not bool(client.get("live_keys_enabled")):
            cursor.execute(
                """
                UPDATE api_clients SET live_keys_enabled=TRUE, commercial_status='live_enabled', updated_at=NOW()
                WHERE id=%s
                """,
                (client_id,),
            )
            cursor.execute(
                """
                UPDATE api_live_access_requests
                SET status='approved', reviewed_at=NOW(), reviewed_by='system:paid_invoice',
                    review_note='Auto-approved after paid API credit invoice', updated_at=NOW()
                WHERE id=(SELECT id FROM api_live_access_requests WHERE client_id=%s AND status='pending' ORDER BY id DESC LIMIT 1)
                """,
                (client_id,),
            )
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES ('system:commercial-worker', 'commercial.invoice.paid', 'api_credit_invoice', %s, %s)
            """,
            (invoice_id, json.dumps({"client_id": client_id, "credits": credits, "tx_hash": tx_hash, "balance_after": next_balance})),
        )
        conn.commit()
        return {"ok": True, "idempotent": bool(existing_ledger), "invoice": _invoice_public(paid), "balance_after": next_balance}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _load_payable_invoices_by_reference(references: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    unique = [item for item in dict.fromkeys(str(value) for value in references if str(value).startswith("api_pay_"))][:500]
    if not unique:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM api_credit_invoices
            WHERE public_reference = ANY(%s) AND status IN ('pending','expired')
            """,
            (unique,),
        )
        return {str(item.get("public_reference") or ""): item for item in _rows_to_dicts(cursor, cursor.fetchall())}
    finally:
        cursor.close()
        conn.close()


def scan_commercial_payments_once(*, page_limit: int = 100, max_pages: int = 5) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    if not commercial_launch_enabled():
        return {"ok": False, "error": "commercial_launch_disabled", "processed": 0}
    if not incoming_enabled():
        return {"ok": False, "error": "treasury_incoming_disabled", "processed": 0}
    owner = f"commercial:{socket.gethostname()}:{os.getpid()}:{time.time_ns()}"
    if not acquire_distributed_lock("api_commercial_payment_scan", owner, ttl_seconds=120):
        return {"ok": False, "error": "commercial_scan_busy", "processed": 0}
    try:
        from services.ton_service import _get_transactions_page, _tx_lt_hash

        transactions: List[Dict[str, Any]] = []
        cursor_lt = ""
        cursor_hash = ""
        for _ in range(max(1, min(int(max_pages), 20))):
            page = _get_transactions_page(max(1, min(int(page_limit), 100)), cursor_lt, cursor_hash)
            if not page:
                break
            transactions.extend(page)
            if len(page) < page_limit:
                break
            cursor_lt, cursor_hash = _tx_lt_hash(page[-1])
            if not cursor_lt or not cursor_hash:
                break
        parsed: List[Tuple[Dict[str, Any], str, Dict[str, Any]]] = []
        for tx in transactions:
            reference, incoming = _invoice_reference_from_tx(tx)
            if reference:
                parsed.append((tx, reference, incoming))
        invoices = _load_payable_invoices_by_reference([item[1] for item in parsed])
        paid = 0
        retryable = 0
        rejected = 0
        errors: List[str] = []
        for tx, reference, incoming in parsed:
            invoice = invoices.get(reference)
            if not invoice:
                continue
            try:
                result = _settle_invoice_from_tx(invoice, tx, incoming)
                if result.get("ok"):
                    paid += 1
                elif result.get("retryable"):
                    retryable += 1
                else:
                    rejected += 1
                    errors.append(str(result.get("error") or "payment_mismatch"))
                    _record_invoice_check_error(str(invoice.get("invoice_id") or ""), str(result.get("error") or "payment_mismatch"))
            except (ApiCommercialError, ApiBillingError) as exc:
                rejected += 1
                errors.append(str(getattr(exc, "code", str(exc))))
                _record_invoice_check_error(str(invoice.get("invoice_id") or ""), str(getattr(exc, "code", str(exc))))
            except Exception as exc:
                logger.exception("API_COMMERCIAL_INVOICE_SETTLEMENT_FAILED reference=%s", reference)
                retryable += 1
                errors.append(type(exc).__name__)
        expire_pending_invoices()
        return {
            "ok": True,
            "transactions_scanned": len(transactions),
            "references_seen": len(parsed),
            "paid": paid,
            "retryable": retryable,
            "rejected": rejected,
            "errors": sorted(set(errors))[:20],
        }
    finally:
        release_distributed_lock("api_commercial_payment_scan", owner)


def _record_invoice_check_error(invoice_id: str, error: str) -> None:
    if not invoice_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE api_credit_invoices SET last_checked_at=NOW(), last_error=%s, updated_at=NOW() WHERE invoice_id=%s AND status<>'paid'",
            (str(error)[:500], str(invoice_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def expire_pending_invoices() -> int:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE api_credit_invoices
            SET status='expired', updated_at=NOW(), last_error=COALESCE(last_error, 'invoice_expired')
            WHERE status='pending' AND expires_at < NOW()
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


def refresh_owned_invoice(*, user_id: int, invoice_id: str) -> Dict[str, Any]:
    invoice = get_owned_credit_invoice(user_id=int(user_id), invoice_id=str(invoice_id))
    if not invoice:
        raise ApiCommercialError("invoice_not_found")
    scan = scan_commercial_payments_once(page_limit=100, max_pages=3)
    refreshed = get_owned_credit_invoice(user_id=int(user_id), invoice_id=str(invoice_id))
    if not refreshed:
        raise ApiCommercialError("invoice_not_found")
    return {"invoice": refreshed, "scan": scan}


def commercial_overview(user_id: int) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    uid = int(user_id)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT c.id, c.name, c.status, c.credit_balance, c.commercial_status,
                   c.live_keys_enabled, c.monthly_spend_limit_credits,
                   c.low_balance_threshold,
                   (SELECT COUNT(*) FROM api_keys k WHERE k.client_id=c.id AND k.status='active' AND k.environment='live') AS active_live_keys
            FROM api_client_owners o JOIN api_clients c ON c.id=o.client_id
            WHERE o.user_id=%s ORDER BY c.id DESC
            """,
            (uid,),
        )
        projects = _rows_to_dicts(cursor, cursor.fetchall())
        for project in projects:
            project["spend"] = monthly_spend_snapshot(int(project.get("id") or 0), cursor=cursor)
            cursor.execute(
                "SELECT * FROM api_live_access_requests WHERE client_id=%s ORDER BY id DESC LIMIT 1",
                (int(project.get("id") or 0),),
            )
            request_row = _row_to_dict(cursor, cursor.fetchone())
            project["live_access_request"] = request_row
        invoices = list_owned_credit_invoices(user_id=uid, limit=100)
        return {
            "enabled": commercial_launch_enabled(),
            "live_keys_enabled": live_keys_globally_enabled(),
            "auto_approve_on_payment": live_auto_approve_on_payment(),
            "treasury_incoming_enabled": incoming_enabled(),
            "network": _runtime_network(),
            "packages": list_credit_packages(include_disabled=False),
            "projects": projects,
            "invoices": invoices,
            "terms_version": "2026-07",
        }
    finally:
        cursor.close()
        conn.close()


def list_live_access_requests(*, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT r.*, c.name AS client_name, c.credit_balance,
                   c.commercial_status, c.live_keys_enabled
            FROM api_live_access_requests r
            JOIN api_clients c ON c.id=r.client_id
            ORDER BY CASE r.status WHEN 'pending' THEN 0 ELSE 1 END, r.id DESC
            LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def list_all_credit_invoices(*, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT i.*, c.name AS client_name
            FROM api_credit_invoices i JOIN api_clients c ON c.id=i.client_id
            ORDER BY i.id DESC LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        rows = _rows_to_dicts(cursor, cursor.fetchall())
        result = []
        for row in rows:
            public = _invoice_public(row)
            public["client_name"] = row.get("client_name")
            public["user_id"] = int(row.get("user_id") or 0)
            result.append(public)
        return result
    finally:
        cursor.close()
        conn.close()


def touch_commercial_worker(worker_id: str, status: str, invoice_id: Optional[str] = None) -> None:
    ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_worker_heartbeats (
                worker_id, worker_type, status, current_job_id, started_at, last_seen_at, metadata_json
            ) VALUES (%s, 'commercial', %s, %s, NOW(), NOW(), %s)
            ON CONFLICT (worker_id) DO UPDATE SET
                worker_type='commercial', status=EXCLUDED.status,
                current_job_id=EXCLUDED.current_job_id,
                last_seen_at=NOW(), metadata_json=EXCLUDED.metadata_json
            """,
            (
                str(worker_id)[:120],
                str(status)[:40],
                str(invoice_id or "")[:80] or None,
                json.dumps({"pid": os.getpid(), "hostname": socket.gethostname(), "network": _runtime_network()}),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_commercial_runtime_health(*, include_workers: bool = False) -> Dict[str, Any]:
    ensure_api_commercial_tables()
    stale = commercial_worker_stale_seconds()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='pending') AS pending,
                COUNT(*) FILTER (WHERE status='expired') AS expired,
                COUNT(*) FILTER (WHERE status='paid' AND paid_at >= NOW() - INTERVAL '24 hours') AS paid_24h,
                COALESCE(SUM(credits) FILTER (WHERE status='paid' AND paid_at >= NOW() - INTERVAL '24 hours'), 0) AS credits_24h,
                EXTRACT(EPOCH FROM (NOW() - MIN(created_at) FILTER (WHERE status='pending'))) AS oldest_pending_age_seconds
            FROM api_credit_invoices
            """
        )
        metrics = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            SELECT worker_id, status, current_job_id, started_at, last_seen_at,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at)) AS heartbeat_age_seconds,
                   (last_seen_at >= NOW() - make_interval(secs => %s)) AS fresh
            FROM api_worker_heartbeats WHERE worker_type='commercial'
            ORDER BY last_seen_at DESC LIMIT 20
            """,
            (stale,),
        )
        workers = _rows_to_dicts(cursor, cursor.fetchall())
        cursor.execute("SELECT COUNT(*) FROM api_live_access_requests WHERE status='pending'")
        request_row = cursor.fetchone()
        live_pending = int((request_row[0] if not isinstance(request_row, dict) else next(iter(request_row.values()))) or 0)
    finally:
        cursor.close()
        conn.close()
    fresh = sum(1 for item in workers if bool(item.get("fresh")))
    warnings: List[str] = []
    if commercial_launch_enabled() and fresh == 0:
        warnings.append("no_fresh_commercial_worker")
    if commercial_launch_enabled() and not incoming_enabled():
        warnings.append("treasury_incoming_disabled")
    result: Dict[str, Any] = {
        "status": "operational" if not warnings else "degraded",
        "enabled": commercial_launch_enabled(),
        "live_keys_enabled": live_keys_globally_enabled(),
        "treasury_incoming_enabled": incoming_enabled(),
        "network": _runtime_network(),
        "worker_available": fresh > 0,
        "fresh_workers": fresh,
        "pending_invoices": int(metrics.get("pending") or 0),
        "expired_invoices": int(metrics.get("expired") or 0),
        "paid_24h": int(metrics.get("paid_24h") or 0),
        "credits_sold_24h": int(metrics.get("credits_24h") or 0),
        "oldest_pending_age_seconds": round(float(metrics.get("oldest_pending_age_seconds") or 0), 1),
        "pending_live_requests": live_pending,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if include_workers:
        result["workers"] = workers
    return result


def run_commercial_worker_forever(worker_id: Optional[str] = None) -> None:
    ensure_api_commercial_tables()
    identity = str(worker_id or f"commercial:{socket.gethostname()}:{os.getpid()}")[:120]
    logger.info("API_COMMERCIAL_WORKER_STARTED worker_id=%s", identity)
    try:
        while True:
            try:
                touch_commercial_worker(identity, "running")
                result = scan_commercial_payments_once(page_limit=100, max_pages=5)
                logger.info("API_COMMERCIAL_SCAN %s", result)
                touch_commercial_worker(identity, "idle")
            except Exception:
                logger.exception("API_COMMERCIAL_SCAN_FAILED")
                try:
                    touch_commercial_worker(identity, "degraded")
                except Exception:
                    pass
            time.sleep(commercial_poll_seconds())
    finally:
        try:
            touch_commercial_worker(identity, "stopped")
        except Exception:
            pass
