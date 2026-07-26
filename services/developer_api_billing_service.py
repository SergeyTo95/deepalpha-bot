import hashlib
import json
import re
import secrets
import threading
from typing import Any, Dict, List, Optional, Tuple

from db.database import get_connection
from services.developer_api_service import create_api_client, ensure_developer_api_tables

DEFAULT_API_PRODUCTS: Tuple[Tuple[str, str, int], ...] = (
    ("opportunity_scan", "Opportunity Scan", 1),
    ("market_data", "Market Data", 1),
    ("quick_analysis", "Quick Analysis", 10),
    ("deep_analysis", "Deep Analysis", 50),
)

_BILLING_TABLES_READY = False
_BILLING_TABLES_LOCK = threading.Lock()
_PRODUCT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$")


class ApiBillingError(ValueError):
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


def normalize_product_code(value: str) -> str:
    product_code = str(value or "").strip().lower()
    if not _PRODUCT_CODE_RE.fullmatch(product_code):
        raise ApiBillingError("invalid_product_code")
    return product_code


def normalize_idempotency_key(value: str) -> str:
    key = str(value or "").strip()
    if not _IDEMPOTENCY_RE.fullmatch(key):
        raise ApiBillingError("invalid_idempotency_key")
    return key


def canonical_request_fingerprint(payload: Any) -> str:
    canonical = json.dumps(
        payload if payload is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_reservation_transition(status: str, action: str) -> Dict[str, Any]:
    current = str(status or "").strip().lower()
    operation = str(action or "").strip().lower()
    if operation == "charge":
        if current == "reserved":
            return {"next_status": "charged", "balance_delta": 0, "event_type": "charge", "idempotent": False}
        if current == "charged":
            return {"next_status": "charged", "balance_delta": 0, "event_type": "charge", "idempotent": True}
        raise ApiBillingError("reservation_not_chargeable", status=current)
    if operation == "refund":
        if current in {"reserved", "charged"}:
            return {"next_status": "refunded", "balance_delta": "units", "event_type": "refund", "idempotent": False}
        if current == "refunded":
            return {"next_status": "refunded", "balance_delta": 0, "event_type": "refund", "idempotent": True}
        raise ApiBillingError("reservation_not_refundable", status=current)
    raise ApiBillingError("invalid_billing_action", action=operation)


def ensure_api_billing_tables() -> None:
    global _BILLING_TABLES_READY
    if _BILLING_TABLES_READY:
        return
    with _BILLING_TABLES_LOCK:
        if _BILLING_TABLES_READY:
            return
        ensure_developer_api_tables()
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_products (
                    product_code TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    unit_price INTEGER NOT NULL DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    metadata_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_credit_reservations (
                    id BIGSERIAL PRIMARY KEY,
                    reservation_id TEXT NOT NULL UNIQUE,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    key_id BIGINT REFERENCES api_keys(id) ON DELETE SET NULL,
                    job_id TEXT,
                    product_code TEXT NOT NULL REFERENCES api_products(product_code),
                    units INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'reserved',
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    charged_at TIMESTAMP,
                    refunded_at TIMESTAMP,
                    UNIQUE(client_id, idempotency_key)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS api_credit_ledger (
                    id BIGSERIAL PRIMARY KEY,
                    client_id BIGINT NOT NULL REFERENCES api_clients(id) ON DELETE CASCADE,
                    reservation_id TEXT,
                    job_id TEXT,
                    event_type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    metadata_json TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(client_id, idempotency_key)
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_credit_ledger_client_created ON api_credit_ledger(client_id, created_at DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_credit_ledger_job ON api_credit_ledger(job_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_credit_reservations_client_created ON api_credit_reservations(client_id, created_at DESC)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_api_credit_reservations_job ON api_credit_reservations(job_id) WHERE job_id IS NOT NULL")

            for product_code, display_name, unit_price in DEFAULT_API_PRODUCTS:
                cursor.execute(
                    """
                    INSERT INTO api_products (product_code, display_name, unit_price, enabled)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (product_code) DO NOTHING
                    """,
                    (product_code, display_name, unit_price),
                )

            cursor.execute(
                """
                INSERT INTO api_credit_ledger (
                    client_id, event_type, amount, balance_after,
                    idempotency_key, metadata_json
                )
                SELECT c.id, 'opening_balance', c.credit_balance, c.credit_balance,
                       'opening_balance:' || c.id::text,
                       '{"source":"billing_migration"}'
                FROM api_clients c
                WHERE c.credit_balance <> 0
                ON CONFLICT (client_id, idempotency_key) DO NOTHING
                """
            )

            cursor.execute(
                """
                CREATE OR REPLACE FUNCTION prevent_api_credit_ledger_mutation()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'api_credit_ledger is append-only';
                END;
                $$ LANGUAGE plpgsql
                """
            )
            cursor.execute("DROP TRIGGER IF EXISTS api_credit_ledger_immutable ON api_credit_ledger")
            cursor.execute(
                """
                CREATE TRIGGER api_credit_ledger_immutable
                BEFORE UPDATE OR DELETE ON api_credit_ledger
                FOR EACH ROW EXECUTE FUNCTION prevent_api_credit_ledger_mutation()
                """
            )
            conn.commit()
            _BILLING_TABLES_READY = True
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()


def _insert_ledger(
    cursor,
    *,
    client_id: int,
    event_type: str,
    amount: int,
    balance_after: int,
    idempotency_key: str,
    reservation_id: Optional[str] = None,
    job_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cursor.execute(
        """
        INSERT INTO api_credit_ledger (
            client_id, reservation_id, job_id, event_type, amount,
            balance_after, idempotency_key, metadata_json
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            int(client_id),
            reservation_id,
            job_id,
            str(event_type),
            int(amount),
            int(balance_after),
            str(idempotency_key),
            json.dumps(metadata or {}, ensure_ascii=False, default=str),
        ),
    )
    return _row_to_dict(cursor, cursor.fetchone()) or {}


def _lock_client(cursor, client_id: int) -> Dict[str, Any]:
    cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (int(client_id),))
    client = _row_to_dict(cursor, cursor.fetchone())
    if not client:
        raise ApiBillingError("client_not_found")
    if str(client.get("status") or "") != "active":
        raise ApiBillingError("client_not_active")
    return client


def _lock_product(cursor, product_code: str) -> Dict[str, Any]:
    cursor.execute("SELECT * FROM api_products WHERE product_code=%s FOR UPDATE", (product_code,))
    product = _row_to_dict(cursor, cursor.fetchone())
    if not product:
        raise ApiBillingError("api_product_not_found", product_code=product_code)
    if not bool(product.get("enabled")):
        raise ApiBillingError("api_product_disabled", product_code=product_code)
    price = int(product.get("unit_price") or 0)
    if price < 0:
        raise ApiBillingError("invalid_api_product_price", product_code=product_code)
    product["unit_price"] = price
    return product


def create_billed_api_client(
    *,
    name: str,
    daily_request_limit: int = 1000,
    monthly_request_limit: int = 20000,
    rate_limit_per_minute: int = 60,
    initial_credits: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_api_billing_tables()
    client = create_api_client(
        name=name,
        daily_request_limit=daily_request_limit,
        monthly_request_limit=monthly_request_limit,
        rate_limit_per_minute=rate_limit_per_minute,
        credit_balance=0,
        metadata=metadata,
    )
    credits = max(0, int(initial_credits))
    if credits:
        adjustment = adjust_api_credits(
            client_id=int(client.get("id") or 0),
            delta=credits,
            reason="Initial API credits",
            idempotency_key=f"opening_grant:{int(client.get('id') or 0)}",
            actor="admin",
        )
        client["credit_balance"] = adjustment["balance_after"]
    return client


def list_api_products() -> List[Dict[str, Any]]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_products ORDER BY product_code")
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def update_api_product(
    *,
    product_code: str,
    display_name: str,
    unit_price: int,
    enabled: bool,
    actor: str = "admin",
) -> Dict[str, Any]:
    ensure_api_billing_tables()
    code = normalize_product_code(product_code)
    name = str(display_name or code).strip()[:120] or code
    price = int(unit_price)
    if price < 0 or price > 1_000_000:
        raise ApiBillingError("invalid_api_product_price")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_products (product_code, display_name, unit_price, enabled)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (product_code) DO UPDATE SET
                display_name=EXCLUDED.display_name,
                unit_price=EXCLUDED.unit_price,
                enabled=EXCLUDED.enabled,
                updated_at=NOW()
            RETURNING *
            """,
            (code, name, price, bool(enabled)),
        )
        product = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'product.update', 'api_product', %s, %s)
            """,
            (str(actor)[:100], code, json.dumps({"unit_price": price, "enabled": bool(enabled)})),
        )
        conn.commit()
        return product
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def adjust_api_credits(
    *,
    client_id: int,
    delta: int,
    reason: str,
    idempotency_key: str,
    actor: str = "admin",
) -> Dict[str, Any]:
    ensure_api_billing_tables()
    amount = int(delta)
    if amount == 0:
        raise ApiBillingError("zero_credit_adjustment")
    key = normalize_idempotency_key(idempotency_key)
    clean_reason = str(reason or "Manual adjustment").strip()[:500] or "Manual adjustment"
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _lock_client(cursor, int(client_id))
        cursor.execute(
            "SELECT * FROM api_credit_ledger WHERE client_id=%s AND idempotency_key=%s",
            (int(client_id), key),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if existing:
            if int(existing.get("amount") or 0) != amount:
                raise ApiBillingError("idempotency_conflict")
            conn.commit()
            return {**existing, "idempotent": True}

        current_balance = int(client.get("credit_balance") or 0)
        next_balance = current_balance + amount
        if next_balance < 0:
            raise ApiBillingError(
                "insufficient_api_credits",
                balance=current_balance,
                requested_debit=abs(amount),
            )
        cursor.execute(
            "UPDATE api_clients SET credit_balance=%s, updated_at=NOW() WHERE id=%s",
            (next_balance, int(client_id)),
        )
        event_type = "admin_grant" if amount > 0 else "admin_debit"
        entry = _insert_ledger(
            cursor,
            client_id=int(client_id),
            event_type=event_type,
            amount=amount,
            balance_after=next_balance,
            idempotency_key=key,
            metadata={"reason": clean_reason, "actor": str(actor)[:100]},
        )
        cursor.execute(
            """
            INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
            VALUES (%s, 'credits.adjust', 'api_client', %s, %s)
            """,
            (
                str(actor)[:100],
                str(client_id),
                json.dumps({"delta": amount, "balance_after": next_balance, "reason": clean_reason}),
            ),
        )
        conn.commit()
        return {**entry, "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def create_billed_api_job(
    *,
    client_id: int,
    key_id: Optional[int],
    job_type: str,
    product_code: str,
    idempotency_key: str,
    request_payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_api_billing_tables()
    code = normalize_product_code(product_code)
    key = normalize_idempotency_key(idempotency_key)
    fingerprint = canonical_request_fingerprint(request_payload)
    clean_job_type = str(job_type or code).strip().lower()[:80] or code
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _lock_client(cursor, int(client_id))
        cursor.execute(
            """
            SELECT * FROM api_credit_reservations
            WHERE client_id=%s AND idempotency_key=%s
            FOR UPDATE
            """,
            (int(client_id), key),
        )
        existing = _row_to_dict(cursor, cursor.fetchone())
        if existing:
            if (
                str(existing.get("request_fingerprint") or "") != fingerprint
                or str(existing.get("product_code") or "") != code
            ):
                raise ApiBillingError("idempotency_conflict")
            cursor.execute("SELECT * FROM api_jobs WHERE job_id=%s", (existing.get("job_id"),))
            job = _row_to_dict(cursor, cursor.fetchone()) or {}
            conn.commit()
            return {
                "ok": True,
                "idempotent": True,
                "job": job,
                "reservation": existing,
                "credit_balance": int(client.get("credit_balance") or 0),
            }

        product = _lock_product(cursor, code)
        units = int(product.get("unit_price") or 0)
        current_balance = int(client.get("credit_balance") or 0)
        if current_balance < units:
            raise ApiBillingError(
                "insufficient_api_credits",
                balance=current_balance,
                required_credits=units,
            )
        next_balance = current_balance - units
        job_id = f"job_{secrets.token_hex(16)}"
        reservation_id = f"res_{secrets.token_hex(16)}"
        cursor.execute(
            "UPDATE api_clients SET credit_balance=%s, updated_at=NOW() WHERE id=%s",
            (next_balance, int(client_id)),
        )
        cursor.execute(
            """
            INSERT INTO api_credit_reservations (
                reservation_id, client_id, key_id, job_id, product_code,
                units, status, idempotency_key, request_fingerprint
            ) VALUES (%s, %s, %s, %s, %s, %s, 'reserved', %s, %s)
            RETURNING *
            """,
            (
                reservation_id,
                int(client_id),
                int(key_id) if key_id else None,
                job_id,
                code,
                units,
                key,
                fingerprint,
            ),
        )
        reservation = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            INSERT INTO api_jobs (
                job_id, client_id, key_id, job_type, status,
                idempotency_key, request_json, units_reserved, units_charged
            ) VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, 0)
            RETURNING *
            """,
            (
                job_id,
                int(client_id),
                int(key_id) if key_id else None,
                clean_job_type,
                key,
                json.dumps(request_payload or {}, ensure_ascii=False, default=str),
                units,
            ),
        )
        job = _row_to_dict(cursor, cursor.fetchone()) or {}
        _insert_ledger(
            cursor,
            client_id=int(client_id),
            reservation_id=reservation_id,
            job_id=job_id,
            event_type="reserve",
            amount=-units,
            balance_after=next_balance,
            idempotency_key=f"reserve:{reservation_id}",
            metadata={"product_code": code, "request_idempotency_key": key},
        )
        conn.commit()
        return {
            "ok": True,
            "idempotent": False,
            "job": job,
            "reservation": reservation,
            "credit_balance": next_balance,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _load_job_context_for_update(cursor, job_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    cursor.execute("SELECT client_id FROM api_jobs WHERE job_id=%s", (str(job_id),))
    reference = _row_to_dict(cursor, cursor.fetchone())
    if not reference:
        raise ApiBillingError("api_job_not_found")
    client = _lock_client(cursor, int(reference.get("client_id") or 0))
    cursor.execute("SELECT * FROM api_jobs WHERE job_id=%s FOR UPDATE", (str(job_id),))
    job = _row_to_dict(cursor, cursor.fetchone())
    cursor.execute("SELECT * FROM api_credit_reservations WHERE job_id=%s FOR UPDATE", (str(job_id),))
    reservation = _row_to_dict(cursor, cursor.fetchone())
    if not job or not reservation:
        raise ApiBillingError("api_job_billing_context_missing")
    return client, job, reservation


def complete_api_job_success(job_id: str, result_payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client, job, reservation = _load_job_context_for_update(cursor, job_id)
        transition = resolve_reservation_transition(str(reservation.get("status") or ""), "charge")
        if transition["idempotent"]:
            conn.commit()
            return {"ok": True, "idempotent": True, "job": job, "reservation": reservation}
        units = int(reservation.get("units") or 0)
        balance = int(client.get("credit_balance") or 0)
        cursor.execute(
            """
            UPDATE api_credit_reservations
            SET status='charged', charged_at=NOW(), updated_at=NOW()
            WHERE reservation_id=%s
            RETURNING *
            """,
            (reservation.get("reservation_id"),),
        )
        reservation = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            UPDATE api_jobs
            SET status='success', result_json=%s, error=NULL,
                units_charged=%s, updated_at=NOW()
            WHERE job_id=%s
            RETURNING *
            """,
            (json.dumps(result_payload or {}, ensure_ascii=False, default=str), units, str(job_id)),
        )
        job = _row_to_dict(cursor, cursor.fetchone()) or {}
        _insert_ledger(
            cursor,
            client_id=int(client.get("id") or client.get("client_id") or reservation.get("client_id") or 0),
            reservation_id=str(reservation.get("reservation_id") or ""),
            job_id=str(job_id),
            event_type="charge",
            amount=0,
            balance_after=balance,
            idempotency_key=f"charge:{reservation.get('reservation_id')}",
            metadata={"units": units},
        )
        conn.commit()
        return {"ok": True, "idempotent": False, "job": job, "reservation": reservation}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def complete_api_job_failure(job_id: str, error: str) -> Dict[str, Any]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client, job, reservation = _load_job_context_for_update(cursor, job_id)
        transition = resolve_reservation_transition(str(reservation.get("status") or ""), "refund")
        if transition["idempotent"]:
            conn.commit()
            return {"ok": True, "idempotent": True, "job": job, "reservation": reservation}
        units = int(reservation.get("units") or 0)
        current_balance = int(client.get("credit_balance") or 0)
        next_balance = current_balance + units
        client_id = int(reservation.get("client_id") or 0)
        cursor.execute(
            "UPDATE api_clients SET credit_balance=%s, updated_at=NOW() WHERE id=%s",
            (next_balance, client_id),
        )
        cursor.execute(
            """
            UPDATE api_credit_reservations
            SET status='refunded', refunded_at=NOW(), updated_at=NOW()
            WHERE reservation_id=%s
            RETURNING *
            """,
            (reservation.get("reservation_id"),),
        )
        reservation = _row_to_dict(cursor, cursor.fetchone()) or {}
        cursor.execute(
            """
            UPDATE api_jobs
            SET status='error', error=%s, units_charged=0, updated_at=NOW()
            WHERE job_id=%s
            RETURNING *
            """,
            (str(error or "internal_error")[:500], str(job_id)),
        )
        job = _row_to_dict(cursor, cursor.fetchone()) or {}
        _insert_ledger(
            cursor,
            client_id=client_id,
            reservation_id=str(reservation.get("reservation_id") or ""),
            job_id=str(job_id),
            event_type="refund",
            amount=units,
            balance_after=next_balance,
            idempotency_key=f"refund:{reservation.get('reservation_id')}",
            metadata={"error": str(error or "internal_error")[:500]},
        )
        conn.commit()
        return {
            "ok": True,
            "idempotent": False,
            "job": job,
            "reservation": reservation,
            "credit_balance": next_balance,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_api_job_for_client(client_id: int, job_id: str) -> Optional[Dict[str, Any]]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM api_jobs WHERE client_id=%s AND job_id=%s",
            (int(client_id), str(job_id)),
        )
        return _row_to_dict(cursor, cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def list_api_credit_ledger(client_id: Optional[int] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        bounded_limit = max(1, min(int(limit), 2000))
        if client_id is None:
            cursor.execute(
                "SELECT * FROM api_credit_ledger ORDER BY id DESC LIMIT %s",
                (bounded_limit,),
            )
        else:
            cursor.execute(
                "SELECT * FROM api_credit_ledger WHERE client_id=%s ORDER BY id DESC LIMIT %s",
                (int(client_id), bounded_limit),
            )
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def list_api_credit_reservations(client_id: Optional[int] = None, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_api_billing_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        bounded_limit = max(1, min(int(limit), 2000))
        if client_id is None:
            cursor.execute(
                "SELECT * FROM api_credit_reservations ORDER BY id DESC LIMIT %s",
                (bounded_limit,),
            )
        else:
            cursor.execute(
                "SELECT * FROM api_credit_reservations WHERE client_id=%s ORDER BY id DESC LIMIT %s",
                (int(client_id), bounded_limit),
            )
        return _rows_to_dicts(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()
