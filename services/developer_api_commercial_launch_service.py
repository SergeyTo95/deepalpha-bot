import json
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urlparse

from db.database import get_connection
from services import developer_api_commercial_service as legacy
from services.developer_api_billing_service import normalize_idempotency_key
from services.developer_api_service import generate_api_key
from services.developer_portal_service import _clean_key_name, _owned_client, max_keys_per_project
from services.ton_chain_service import normalize_ton_address
from services.treasury_service import get_public_treasury_address, incoming_enabled, ton_network_id


LIVE_STATES = {
    "test_only",
    "live_requested",
    "live_approved",
    "live_rejected",
    "live_suspended",
}
INVOICE_STATES = {
    "pending",
    "awaiting_payment",
    "payment_detected",
    "paid",
    "crediting",
    "credited",
    "expired",
    "cancelled",
    "failed",
    "refunded",
}
OPEN_INVOICE_STATES = {"pending", "awaiting_payment", "payment_detected", "paid", "crediting"}
PAYABLE_INVOICE_STATES = {"pending", "awaiting_payment", "payment_detected", "expired"}
LIVE_ALLOWED_SCOPES = {
    "account:read",
    "usage:read",
    "analysis:run",
    "analysis:read",
    "opportunities:run",
    "opportunities:read",
    "webhooks:manage",
    "markets:read",
}
_TABLES_READY = False


class CommercialLaunchError(ValueError):
    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = str(code)
        self.details = details


def _row(cursor, value) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    columns = [item[0] for item in (cursor.description or [])]
    return dict(zip(columns, value))


def _rows(cursor, values) -> List[Dict[str, Any]]:
    return [item for item in (_row(cursor, value) for value in values or []) if item]


def _env_true(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def credit_purchases_enabled() -> bool:
    return legacy.commercial_launch_enabled() and _env_true("API_CREDIT_PURCHASES_ENABLED", True)


def invoice_provider_name() -> str:
    value = str(os.getenv("API_CREDIT_INVOICE_PROVIDER", "ton_treasury") or "ton_treasury").strip().lower()
    return value if value in {"manual", "ton_treasury"} else "manual"


def invoice_ttl_hours() -> int:
    raw = os.getenv("API_CREDIT_INVOICE_TTL_HOURS")
    if raw not in (None, ""):
        return _env_int("API_CREDIT_INVOICE_TTL_HOURS", 24, 1, 168)
    minutes = legacy.invoice_ttl_minutes()
    return max(1, min(168, (minutes + 59) // 60))


def maximum_open_invoices() -> int:
    return _env_int("API_CREDIT_MAX_OPEN_INVOICES", 3, 1, 20)


def live_minimum_balance() -> int:
    return _env_int("API_LIVE_MINIMUM_BALANCE", 10, 0, 1_000_000_000)


def _configured_currency() -> str:
    value = str(os.getenv("API_CREDIT_CURRENCY", "TON") or "TON").strip().upper()
    return value[:12] or "TON"


def _decimal(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CommercialLaunchError("invalid_price_amount") from exc
    if amount <= 0 or amount > Decimal("1000000000000"):
        raise CommercialLaunchError("invalid_price_amount")
    return amount


def _amount_text(value: Any) -> str:
    amount = _decimal(value)
    return format(amount.normalize(), "f")


def _ton_to_nano(value: Any) -> int:
    amount = _decimal(value)
    nano = int((amount * Decimal(1_000_000_000)).to_integral_exact())
    if nano <= 0:
        raise CommercialLaunchError("invalid_price_amount")
    return nano


def _clean_text(value: Any, *, minimum: int, maximum: int, code: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) < minimum or len(text) > maximum:
        raise CommercialLaunchError(code)
    return text


def _optional_text(value: Any, maximum: int) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _validate_website(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > 300:
        raise CommercialLaunchError("invalid_website")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise CommercialLaunchError("invalid_website")
    return text


def _normalize_live_scopes(scopes: Optional[Iterable[str]]) -> List[str]:
    requested = ["account:read", "usage:read"] if scopes is None else list(scopes)
    result: List[str] = []
    for raw in requested:
        scope = str(raw or "").strip().lower()
        if scope.startswith("wallet:") or scope in {"wallet:send", "wallet:withdraw", "trading:execute"}:
            raise CommercialLaunchError("scope_not_available", scope=scope)
        if scope not in LIVE_ALLOWED_SCOPES:
            raise CommercialLaunchError("scope_not_available", scope=scope)
        if scope not in result:
            result.append(scope)
    if not result:
        raise CommercialLaunchError("at_least_one_scope_required")
    return result


def _audit(cursor, actor: str, action: str, target_type: str, target_id: Any, metadata: Dict[str, Any]) -> None:
    cursor.execute(
        """
        INSERT INTO api_audit_log (actor, action, target_type, target_id, metadata_json)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            str(actor)[:100],
            str(action)[:120],
            str(target_type)[:80],
            str(target_id)[:120],
            json.dumps(metadata or {}, ensure_ascii=False, default=str),
        ),
    )


def _payment_event(
    cursor,
    *,
    invoice_id: str,
    event_type: str,
    actor: str,
    from_status: Optional[str],
    to_status: Optional[str],
    reference: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
) -> None:
    event_id = f"pevt_{secrets.token_hex(16)}"
    key = str(idempotency_key or f"{invoice_id}:{event_type}:{to_status or ''}:{reference or ''}")[:240]
    cursor.execute(
        """
        INSERT INTO api_payment_events (
            event_id, invoice_id, event_type, actor, from_status, to_status,
            payment_reference, metadata_json, idempotency_key
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        (
            event_id,
            invoice_id,
            str(event_type)[:80],
            str(actor)[:100],
            from_status,
            to_status,
            str(reference or "")[:240] or None,
            json.dumps(metadata or {}, ensure_ascii=False, default=str),
            key,
        ),
    )


class PaymentProviderAdapter(ABC):
    name = "unknown"

    @abstractmethod
    def create_checkout(self, *, invoice_id: str, amount: Decimal, currency: str) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def verify_payment(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def normalize_webhook(self, payload: Any) -> Dict[str, Any]:
        raise CommercialLaunchError("provider_webhook_not_supported")

    def payment_reference(self, invoice_id: str) -> str:
        return f"api_pay_{secrets.token_hex(16)}"


class ManualPaymentAdapter(PaymentProviderAdapter):
    name = "manual"

    def create_checkout(self, *, invoice_id: str, amount: Decimal, currency: str) -> Dict[str, Any]:
        reference = self.payment_reference(invoice_id)
        address = str(os.getenv("API_CREDIT_PAYMENT_ADDRESS", "") or "").strip()
        instructions = str(os.getenv("API_CREDIT_MANUAL_PAYMENT_INSTRUCTIONS", "Contact support and include the invoice reference") or "").strip()
        return {
            "payment_reference": reference,
            "payment_address": address or None,
            "checkout_url": None,
            "provider_metadata": {"instructions": instructions[:1000], "verification": "manual_admin"},
            "treasury_wallet_id": 0,
            "network": "manual",
            "legacy_price_nano": _ton_to_nano(amount) if currency == "TON" else 1,
        }

    def verify_payment(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": False, "error": "manual_review_required", "retryable": False}


class TonTreasuryPaymentAdapter(PaymentProviderAdapter):
    name = "ton_treasury"

    def create_checkout(self, *, invoice_id: str, amount: Decimal, currency: str) -> Dict[str, Any]:
        if currency != "TON":
            raise CommercialLaunchError("provider_currency_not_supported", provider=self.name, currency=currency)
        if not incoming_enabled():
            raise CommercialLaunchError("treasury_incoming_disabled")
        treasury = get_public_treasury_address()
        if not treasury.get("ok"):
            raise CommercialLaunchError(str(treasury.get("error") or "treasury_not_configured"))
        reference = self.payment_reference(invoice_id)
        price_nano = _ton_to_nano(amount)
        address = str(treasury.get("address") or "")
        network = legacy._runtime_network()
        return {
            "payment_reference": reference,
            "payment_address": address,
            "checkout_url": f"ton://transfer/{quote(address, safe='')}?amount={price_nano}&text={quote(reference, safe='')}",
            "provider_metadata": {"network_id": ton_network_id(network), "verification": "ton_treasury_scanner"},
            "treasury_wallet_id": int(treasury.get("wallet_id") or 0),
            "network": network,
            "legacy_price_nano": price_nano,
        }

    def verify_payment(self, invoice: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": False, "error": "worker_verification_required", "retryable": True}


def payment_adapter(name: Optional[str] = None) -> PaymentProviderAdapter:
    provider = str(name or invoice_provider_name()).strip().lower()
    if provider == "ton_treasury":
        return TonTreasuryPaymentAdapter()
    if provider == "manual":
        return ManualPaymentAdapter()
    raise CommercialLaunchError("payment_provider_not_supported")


def ensure_commercial_launch_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    legacy.ensure_api_commercial_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS daily_spend_limit_credits INTEGER")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS auto_recharge_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS auto_recharge_package_code TEXT")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS last_low_balance_notified_at TIMESTAMP")
        cursor.execute("ALTER TABLE api_clients ADD COLUMN IF NOT EXISTS last_low_balance_notified_balance INTEGER")
        cursor.execute("ALTER TABLE api_clients ALTER COLUMN low_balance_threshold DROP NOT NULL")
        cursor.execute("ALTER TABLE api_clients ALTER COLUMN monthly_spend_limit_credits DROP NOT NULL")
        cursor.execute(
            """
            UPDATE api_clients SET commercial_status=CASE commercial_status
                WHEN 'live_review' THEN 'live_requested'
                WHEN 'live_enabled' THEN 'live_approved'
                ELSE commercial_status END
            WHERE commercial_status IN ('live_review','live_enabled')
            """
        )

        cursor.execute("ALTER TABLE api_credit_packages ADD COLUMN IF NOT EXISTS price_amount NUMERIC(38,18)")
        cursor.execute("ALTER TABLE api_credit_packages ADD COLUMN IF NOT EXISTS price_currency TEXT")
        cursor.execute(
            """
            UPDATE api_credit_packages
            SET price_amount=COALESCE(price_amount, price_nano::numeric / 1000000000),
                price_currency=COALESCE(NULLIF(price_currency,''), 'TON')
            WHERE price_amount IS NULL OR price_currency IS NULL OR price_currency=''
            """
        )

        for statement in (
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS owner_user_id BIGINT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS amount NUMERIC(38,18)",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS currency TEXT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS payment_provider TEXT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS payment_reference TEXT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS payment_address TEXT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS checkout_url TEXT",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS payment_detected_at TIMESTAMP",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS credited_at TIMESTAMP",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS failed_at TIMESTAMP",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMP",
            "ALTER TABLE api_credit_invoices ADD COLUMN IF NOT EXISTS provider_metadata_json TEXT",
        ):
            cursor.execute(statement)
        cursor.execute(
            """
            UPDATE api_credit_invoices SET
                owner_user_id=COALESCE(owner_user_id, user_id),
                amount=COALESCE(amount, price_nano::numeric / 1000000000),
                currency=COALESCE(NULLIF(currency,''), 'TON'),
                payment_provider=COALESCE(NULLIF(payment_provider,''), 'ton_treasury'),
                payment_reference=COALESCE(NULLIF(payment_reference,''), public_reference),
                payment_address=COALESCE(NULLIF(payment_address,''), treasury_address),
                credited_at=CASE WHEN status='paid' THEN COALESCE(credited_at, paid_at) ELSE credited_at END,
                status=CASE WHEN status='pending' THEN 'awaiting_payment' WHEN status='paid' THEN 'credited' ELSE status END
            WHERE owner_user_id IS NULL OR amount IS NULL OR currency IS NULL OR payment_provider IS NULL
               OR payment_reference IS NULL OR status IN ('pending','paid')
            """
        )
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_credit_invoice_payment_reference ON api_credit_invoices(payment_reference) WHERE payment_reference IS NOT NULL")

        for statement in (
            "ALTER TABLE api_live_access_requests ADD COLUMN IF NOT EXISTS company_name TEXT",
            "ALTER TABLE api_live_access_requests ADD COLUMN IF NOT EXISTS website TEXT",
            "ALTER TABLE api_live_access_requests ADD COLUMN IF NOT EXISTS contact TEXT",
            "ALTER TABLE api_live_access_requests ADD COLUMN IF NOT EXISTS admin_comment TEXT",
        ):
            cursor.execute(statement)
        cursor.execute(
            """
            UPDATE api_live_access_requests SET status=CASE status
                WHEN 'pending' THEN 'live_requested'
                WHEN 'approved' THEN 'live_approved'
                WHEN 'rejected' THEN 'live_rejected'
                ELSE status END,
                admin_comment=COALESCE(admin_comment, review_note)
            WHERE status IN ('pending','approved','rejected') OR admin_comment IS NULL
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS api_payment_events (
                id BIGSERIAL PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                invoice_id TEXT NOT NULL REFERENCES api_credit_invoices(invoice_id) ON DELETE RESTRICT,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                payment_reference TEXT,
                metadata_json TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_payment_events_invoice ON api_payment_events(invoice_id, id DESC)")
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_api_payment_event_mutation()
            RETURNS trigger AS $$ BEGIN
                RAISE EXCEPTION 'api_payment_events_append_only';
            END; $$ LANGUAGE plpgsql
            """
        )
        cursor.execute("DROP TRIGGER IF EXISTS trg_api_payment_events_append_only ON api_payment_events")
        cursor.execute(
            """
            CREATE TRIGGER trg_api_payment_events_append_only
            BEFORE UPDATE OR DELETE ON api_payment_events
            FOR EACH ROW EXECUTE FUNCTION prevent_api_payment_event_mutation()
            """
        )

        cursor.execute("DROP TRIGGER IF EXISTS trg_enforce_api_monthly_spend_limit ON api_credit_reservations")
        cursor.execute("DROP TRIGGER IF EXISTS trg_enforce_api_credit_spend_limits ON api_credit_reservations")
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION enforce_api_credit_spend_limits()
            RETURNS trigger AS $$
            DECLARE
                v_daily INTEGER;
                v_monthly INTEGER;
                v_daily_used BIGINT;
                v_monthly_used BIGINT;
            BEGIN
                SELECT daily_spend_limit_credits, monthly_spend_limit_credits
                  INTO v_daily, v_monthly
                FROM api_clients WHERE id=NEW.client_id FOR UPDATE;

                IF COALESCE(v_daily, 0) > 0 THEN
                    SELECT COALESCE(SUM(units), 0) INTO v_daily_used
                    FROM api_credit_reservations
                    WHERE client_id=NEW.client_id AND status IN ('reserved','charged')
                      AND created_at >= date_trunc('day', NOW());
                    IF v_daily_used + NEW.units > v_daily THEN
                        RAISE EXCEPTION 'daily_credit_spend_limit_reached:%:%:%', v_daily, v_daily_used, NEW.units;
                    END IF;
                END IF;

                IF COALESCE(v_monthly, 0) > 0 THEN
                    SELECT COALESCE(SUM(units), 0) INTO v_monthly_used
                    FROM api_credit_reservations
                    WHERE client_id=NEW.client_id AND status IN ('reserved','charged')
                      AND created_at >= date_trunc('month', NOW());
                    IF v_monthly_used + NEW.units > v_monthly THEN
                        RAISE EXCEPTION 'monthly_credit_spend_limit_reached:%:%:%', v_monthly, v_monthly_used, NEW.units;
                    END IF;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
            """
        )
        cursor.execute(
            """
            CREATE TRIGGER trg_enforce_api_credit_spend_limits
            BEFORE INSERT ON api_credit_reservations
            FOR EACH ROW EXECUTE FUNCTION enforce_api_credit_spend_limits()
            """
        )
        conn.commit()
        _TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_credit_packages(*, include_disabled: bool = False) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        where = "" if include_disabled else "WHERE enabled=TRUE"
        cursor.execute(f"SELECT * FROM api_credit_packages {where} ORDER BY sort_order, credits, id")
        result = []
        for item in _rows(cursor, cursor.fetchall()):
            metadata = item.pop("metadata_json", None)
            result.append({
                "package_code": str(item.get("package_code") or ""),
                "display_name": str(item.get("display_name") or ""),
                "credits": int(item.get("credits") or 0),
                "price_amount": _amount_text(item.get("price_amount") or (Decimal(int(item.get("price_nano") or 0)) / Decimal(1_000_000_000))),
                "price_currency": str(item.get("price_currency") or "TON"),
                "enabled": bool(item.get("enabled")),
                "sort_order": int(item.get("sort_order") or 0),
                "metadata": json.loads(metadata) if isinstance(metadata, str) and metadata else {},
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            })
        return result
    finally:
        cursor.close()
        conn.close()


def upsert_credit_package(
    *, package_code: str, display_name: str, credits: int, price_amount: Any,
    price_currency: str, enabled: bool, sort_order: int, metadata: Optional[Dict[str, Any]], actor: str,
) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    code = legacy._clean_package_code(package_code)
    name = legacy._clean_package_name(display_name)
    units = int(credits)
    if units <= 0 or units > 1_000_000_000:
        raise CommercialLaunchError("invalid_package_credits")
    currency = str(price_currency or "").strip().upper()
    if currency != _configured_currency():
        raise CommercialLaunchError("unsupported_launch_currency", configured_currency=_configured_currency())
    amount = _decimal(price_amount)
    price_nano = _ton_to_nano(amount) if currency == "TON" else 1
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO api_credit_packages (
                package_code, display_name, credits, price_nano, price_amount,
                price_currency, enabled, sort_order, metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (package_code) DO UPDATE SET
                display_name=EXCLUDED.display_name,
                credits=EXCLUDED.credits,
                price_nano=EXCLUDED.price_nano,
                price_amount=EXCLUDED.price_amount,
                price_currency=EXCLUDED.price_currency,
                enabled=EXCLUDED.enabled,
                sort_order=EXCLUDED.sort_order,
                metadata_json=EXCLUDED.metadata_json,
                updated_at=NOW()
            RETURNING *
            """,
            (code, name, units, price_nano, str(amount), currency, bool(enabled), int(sort_order), json.dumps(metadata or {}, ensure_ascii=False)),
        )
        row = _row(cursor, cursor.fetchone()) or {}
        _audit(cursor, actor, "commercial.package.upsert", "api_credit_package", code, {
            "credits": units, "price_amount": _amount_text(amount), "price_currency": currency, "enabled": bool(enabled)
        })
        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _invoice_public(item: Dict[str, Any]) -> Dict[str, Any]:
    provider_metadata = {}
    try:
        provider_metadata = json.loads(str(item.get("provider_metadata_json") or "{}"))
    except Exception:
        provider_metadata = {}
    result = {
        "invoice_id": str(item.get("invoice_id") or ""),
        "client_id": int(item.get("client_id") or 0),
        "package_code": str(item.get("package_code") or ""),
        "package_name": str(item.get("package_name") or ""),
        "credits": int(item.get("credits") or 0),
        "amount": _amount_text(item.get("amount") or (Decimal(int(item.get("price_nano") or 0)) / Decimal(1_000_000_000))),
        "currency": str(item.get("currency") or "TON"),
        "status": str(item.get("status") or "awaiting_payment"),
        "payment_provider": str(item.get("payment_provider") or "manual"),
        "payment_reference": str(item.get("payment_reference") or item.get("public_reference") or ""),
        "payment_address": item.get("payment_address") or item.get("treasury_address"),
        "checkout_url": item.get("checkout_url"),
        "payment_instructions": str(provider_metadata.get("instructions") or ""),
        "expires_at": item.get("expires_at"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "payment_detected_at": item.get("payment_detected_at"),
        "paid_at": item.get("paid_at"),
        "credited_at": item.get("credited_at"),
        "cancelled_at": item.get("cancelled_at"),
        "last_checked_at": item.get("last_checked_at"),
        "last_error": item.get("last_error"),
        "tx_hash": item.get("tx_hash"),
    }
    if result["payment_provider"] == "ton_treasury" and result["payment_address"] and result["payment_reference"]:
        nano = int(item.get("price_nano") or _ton_to_nano(result["amount"]))
        result["checkout_url"] = result["checkout_url"] or f"ton://transfer/{quote(str(result['payment_address']), safe='')}?amount={nano}&text={quote(result['payment_reference'], safe='')}"
    return result


def create_credit_invoice(
    *, user_id: int, client_id: int, package_code: str, idempotency_key: str,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    if not credit_purchases_enabled():
        raise CommercialLaunchError("credit_purchases_disabled")
    key = normalize_idempotency_key(idempotency_key)
    code = legacy._clean_package_code(package_code)
    provider_adapter = payment_adapter(provider)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise CommercialLaunchError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise CommercialLaunchError("project_not_active")
        cursor.execute("SELECT * FROM api_credit_invoices WHERE user_id=%s AND idempotency_key=%s FOR UPDATE", (int(user_id), key))
        existing = _row(cursor, cursor.fetchone())
        if existing:
            expected = json.dumps({"client_id": int(client_id), "package_code": code, "provider": provider_adapter.name}, sort_keys=True)
            stored = str(existing.get("request_fingerprint") or "")
            fingerprint = legacy._canonical_fingerprint(json.loads(expected))
            if stored != fingerprint:
                raise CommercialLaunchError("idempotency_conflict")
            conn.commit()
            return {**_invoice_public(existing), "idempotent": True}
        cursor.execute("SELECT COUNT(*) FROM api_credit_invoices WHERE client_id=%s AND status=ANY(%s)", (int(client_id), list(OPEN_INVOICE_STATES)))
        count_row = cursor.fetchone()
        count = int((count_row[0] if not isinstance(count_row, dict) else next(iter(count_row.values()))) or 0)
        if count >= maximum_open_invoices():
            raise CommercialLaunchError("open_invoice_limit_reached", limit=maximum_open_invoices())
        cursor.execute("SELECT * FROM api_credit_packages WHERE package_code=%s AND enabled=TRUE FOR UPDATE", (code,))
        package = _row(cursor, cursor.fetchone())
        if not package:
            raise CommercialLaunchError("credit_package_not_found")
        currency = str(package.get("price_currency") or "TON").upper()
        amount = _decimal(package.get("price_amount") or (Decimal(int(package.get("price_nano") or 0)) / Decimal(1_000_000_000)))
        invoice_id = f"inv_{secrets.token_hex(18)}"
        checkout = provider_adapter.create_checkout(invoice_id=invoice_id, amount=amount, currency=currency)
        reference = str(checkout.get("payment_reference") or provider_adapter.payment_reference(invoice_id))
        fingerprint = legacy._canonical_fingerprint({"client_id": int(client_id), "package_code": code, "provider": provider_adapter.name})
        cursor.execute(
            """
            INSERT INTO api_credit_invoices (
                invoice_id, public_reference, payment_reference, user_id, owner_user_id,
                client_id, package_code, package_name, credits, price_nano, amount,
                currency, treasury_wallet_id, treasury_address, payment_address,
                checkout_url, network, status, payment_provider, idempotency_key,
                request_fingerprint, expires_at, metadata_json, provider_metadata_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                'awaiting_payment',%s,%s,%s,NOW() + make_interval(hours => %s),%s,%s
            ) RETURNING *
            """,
            (
                invoice_id, reference, reference, int(user_id), int(user_id), int(client_id), code,
                str(package.get("display_name") or code), int(package.get("credits") or 0),
                int(checkout.get("legacy_price_nano") or package.get("price_nano") or 1), str(amount), currency,
                int(checkout.get("treasury_wallet_id") or 0), str(checkout.get("payment_address") or "manual"),
                checkout.get("payment_address"), checkout.get("checkout_url"), str(checkout.get("network") or "manual"),
                provider_adapter.name, key, fingerprint, invoice_ttl_hours(),
                json.dumps({"package_id": package.get("id")}, ensure_ascii=False),
                json.dumps(checkout.get("provider_metadata") or {}, ensure_ascii=False),
            ),
        )
        invoice = _row(cursor, cursor.fetchone()) or {}
        _payment_event(cursor, invoice_id=invoice_id, event_type="invoice.created", actor=f"user:{int(user_id)}", from_status=None, to_status="awaiting_payment", reference=reference)
        _audit(cursor, f"user:{int(user_id)}", "commercial.invoice.create", "api_credit_invoice", invoice_id, {
            "client_id": int(client_id), "package_code": code, "credits": invoice.get("credits"),
            "amount": _amount_text(amount), "currency": currency, "provider": provider_adapter.name,
        })
        conn.commit()
        return {**_invoice_public(invoice), "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_owned_invoices(*, user_id: int, client_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        params: List[Any] = [int(user_id)]
        clause = ""
        if client_id is not None:
            clause = " AND i.client_id=%s"
            params.append(int(client_id))
        params.append(max(1, min(int(limit), 200)))
        cursor.execute(
            f"""SELECT i.* FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE o.user_id=%s {clause} ORDER BY i.id DESC LIMIT %s""",
            tuple(params),
        )
        return [_invoice_public(item) for item in _rows(cursor, cursor.fetchall())]
    finally:
        cursor.close()
        conn.close()


def get_owned_invoice(*, user_id: int, invoice_id: str) -> Optional[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT i.* FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE o.user_id=%s AND i.invoice_id=%s""",
            (int(user_id), str(invoice_id)),
        )
        item = _row(cursor, cursor.fetchone())
        return _invoice_public(item) if item else None
    finally:
        cursor.close()
        conn.close()


def cancel_owned_invoice(*, user_id: int, invoice_id: str) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT i.* FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE o.user_id=%s AND i.invoice_id=%s FOR UPDATE OF i""",
            (int(user_id), str(invoice_id)),
        )
        invoice = _row(cursor, cursor.fetchone())
        if not invoice:
            raise CommercialLaunchError("invoice_not_found")
        status = str(invoice.get("status") or "")
        if status not in {"pending", "awaiting_payment"}:
            raise CommercialLaunchError("invoice_not_cancellable")
        cursor.execute("UPDATE api_credit_invoices SET status='cancelled', cancelled_at=NOW(), updated_at=NOW() WHERE invoice_id=%s RETURNING *", (str(invoice_id),))
        updated = _row(cursor, cursor.fetchone()) or {}
        _payment_event(cursor, invoice_id=str(invoice_id), event_type="invoice.cancelled", actor=f"user:{int(user_id)}", from_status=status, to_status="cancelled")
        _audit(cursor, f"user:{int(user_id)}", "commercial.invoice.cancel", "api_credit_invoice", invoice_id, {})
        conn.commit()
        return _invoice_public(updated)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _credit_locked_invoice(cursor, invoice: Dict[str, Any], *, actor: str, reference: Optional[str]) -> Dict[str, Any]:
    invoice_id = str(invoice.get("invoice_id") or "")
    client_id = int(invoice.get("client_id") or 0)
    cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (client_id,))
    client = _row(cursor, cursor.fetchone())
    if not client or str(client.get("status") or "") != "active":
        raise CommercialLaunchError("client_not_active")
    cursor.execute("SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE", (invoice_id,))
    locked = _row(cursor, cursor.fetchone()) or {}
    if locked.get("credited_at") is not None or str(locked.get("status") or "") == "credited":
        return {"invoice": locked, "balance_after": int(client.get("credit_balance") or 0), "idempotent": True}
    status = str(locked.get("status") or "")
    if status not in {"payment_detected", "paid", "crediting"}:
        raise CommercialLaunchError("invoice_not_creditable", status=status)
    cursor.execute("UPDATE api_credit_invoices SET status='crediting', updated_at=NOW() WHERE invoice_id=%s", (invoice_id,))
    _payment_event(cursor, invoice_id=invoice_id, event_type="invoice.crediting", actor=actor, from_status=status, to_status="crediting", reference=reference)
    ledger_key = f"invoice:{invoice_id}"
    cursor.execute("SELECT * FROM api_credit_ledger WHERE client_id=%s AND idempotency_key=%s", (client_id, ledger_key))
    existing = _row(cursor, cursor.fetchone())
    current_balance = int(client.get("credit_balance") or 0)
    credits = int(locked.get("credits") or 0)
    if existing:
        next_balance = int(existing.get("balance_after") or current_balance)
        idempotent = True
    else:
        next_balance = current_balance + credits
        cursor.execute("UPDATE api_clients SET credit_balance=%s, updated_at=NOW() WHERE id=%s", (next_balance, client_id))
        cursor.execute(
            """INSERT INTO api_credit_ledger (
                client_id, event_type, amount, balance_after, idempotency_key, metadata_json
            ) VALUES (%s,'purchase',%s,%s,%s,%s)""",
            (client_id, credits, next_balance, ledger_key, json.dumps({
                "invoice_id": invoice_id, "package_code": locked.get("package_code"),
                "payment_provider": locked.get("payment_provider"), "payment_reference": reference,
                "amount": str(locked.get("amount") or ""), "currency": locked.get("currency"),
            }, ensure_ascii=False)),
        )
        idempotent = False
    cursor.execute(
        """UPDATE api_credit_invoices SET status='credited', credited_at=COALESCE(credited_at,NOW()),
        paid_at=COALESCE(paid_at,NOW()), updated_at=NOW(), last_error=NULL
        WHERE invoice_id=%s RETURNING *""",
        (invoice_id,),
    )
    credited = _row(cursor, cursor.fetchone()) or {}
    _payment_event(cursor, invoice_id=invoice_id, event_type="invoice.credited", actor=actor, from_status="crediting", to_status="credited", reference=reference, metadata={"credits": credits, "balance_after": next_balance}, idempotency_key=f"{invoice_id}:credited")
    _audit(cursor, actor, "commercial.invoice.credited", "api_credit_invoice", invoice_id, {
        "client_id": client_id, "credits": credits, "balance_after": next_balance, "idempotent": idempotent,
    })
    return {"invoice": credited, "balance_after": next_balance, "idempotent": idempotent}


def mark_invoice_paid_admin(*, invoice_id: str, actor: str, payment_reference: str = "") -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE", (str(invoice_id),))
        invoice = _row(cursor, cursor.fetchone())
        if not invoice:
            raise CommercialLaunchError("invoice_not_found")
        status = str(invoice.get("status") or "")
        if status == "credited":
            conn.commit()
            return {"invoice": _invoice_public(invoice), "idempotent": True}
        if status not in {"pending", "awaiting_payment", "payment_detected", "paid"}:
            raise CommercialLaunchError("invoice_not_payable", status=status)
        cursor.execute(
            """UPDATE api_credit_invoices SET status='paid', paid_at=COALESCE(paid_at,NOW()),
            payment_reference=COALESCE(NULLIF(%s,''),payment_reference), updated_at=NOW(), last_error=NULL
            WHERE invoice_id=%s RETURNING *""",
            (str(payment_reference)[:240], str(invoice_id)),
        )
        paid = _row(cursor, cursor.fetchone()) or {}
        _payment_event(cursor, invoice_id=str(invoice_id), event_type="invoice.mark_paid", actor=actor, from_status=status, to_status="paid", reference=payment_reference, idempotency_key=f"{invoice_id}:admin-paid")
        _audit(cursor, actor, "commercial.invoice.mark_paid", "api_credit_invoice", invoice_id, {"payment_reference": str(payment_reference)[:240]})
        conn.commit()
        return {"invoice": _invoice_public(paid), "idempotent": status == "paid"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def credit_invoice_admin(*, invoice_id: str, actor: str) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE", (str(invoice_id),))
        invoice = _row(cursor, cursor.fetchone())
        if not invoice:
            raise CommercialLaunchError("invoice_not_found")
        result = _credit_locked_invoice(cursor, invoice, actor=actor, reference=str(invoice.get("payment_reference") or ""))
        conn.commit()
        return {"invoice": _invoice_public(result["invoice"]), "balance_after": result["balance_after"], "idempotent": result["idempotent"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def cancel_invoice_admin(*, invoice_id: str, actor: str, reason: str = "") -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_invoices WHERE invoice_id=%s FOR UPDATE", (str(invoice_id),))
        invoice = _row(cursor, cursor.fetchone())
        if not invoice:
            raise CommercialLaunchError("invoice_not_found")
        status = str(invoice.get("status") or "")
        if status in {"credited", "refunded"}:
            raise CommercialLaunchError("invoice_not_cancellable")
        if status == "cancelled":
            conn.commit()
            return {"invoice": _invoice_public(invoice), "idempotent": True}
        cursor.execute("UPDATE api_credit_invoices SET status='cancelled', cancelled_at=NOW(), last_error=%s, updated_at=NOW() WHERE invoice_id=%s RETURNING *", (str(reason or "admin_cancelled")[:500], str(invoice_id)))
        cancelled = _row(cursor, cursor.fetchone()) or {}
        _payment_event(cursor, invoice_id=str(invoice_id), event_type="invoice.admin_cancel", actor=actor, from_status=status, to_status="cancelled", metadata={"reason": str(reason)[:500]})
        _audit(cursor, actor, "commercial.invoice.admin_cancel", "api_credit_invoice", invoice_id, {"reason": str(reason)[:500]})
        conn.commit()
        return {"invoice": _invoice_public(cancelled), "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _transaction_time(tx: Dict[str, Any]) -> int:
    return int(tx.get("utime") or tx.get("timestamp") or 0)


def settle_invoice_from_ton(invoice: Dict[str, Any], tx: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    invoice_id = str(invoice.get("invoice_id") or "")
    tx_hash = legacy._transaction_hash(tx)
    if not invoice_id or not tx_hash:
        return {"ok": False, "error": "transaction_identity_missing"}
    source = normalize_ton_address(str(incoming.get("source") or incoming.get("src") or ""))
    destination = normalize_ton_address(str(incoming.get("destination") or incoming.get("dest") or ""))
    expected_destination = normalize_ton_address(str(invoice.get("payment_address") or invoice.get("treasury_address") or ""))
    try:
        amount_nano = int(incoming.get("value") or incoming.get("amount") or 0)
    except Exception:
        amount_nano = 0
    tx_time = _transaction_time(tx)
    if not legacy._transaction_success(tx):
        return {"ok": False, "error": "transaction_failed"}
    if destination != expected_destination:
        return {"ok": False, "error": "destination_mismatch"}
    if amount_nano != int(invoice.get("price_nano") or 0):
        return {"ok": False, "error": "amount_mismatch"}
    if str(invoice.get("network") or "") != legacy._runtime_network():
        return {"ok": False, "error": "network_mismatch"}
    if tx_time and int(time.time()) - tx_time < legacy.confirmation_seconds():
        return {"ok": False, "error": "awaiting_confirmation", "retryable": True}
    created = invoice.get("created_at")
    expires = invoice.get("expires_at")
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
        locked = _row(cursor, cursor.fetchone())
        if not locked:
            raise CommercialLaunchError("invoice_not_found")
        if locked.get("credited_at") is not None or str(locked.get("status") or "") == "credited":
            conn.commit()
            return {"ok": True, "idempotent": True, "invoice": _invoice_public(locked)}
        status = str(locked.get("status") or "")
        if status not in PAYABLE_INVOICE_STATES:
            raise CommercialLaunchError("invoice_not_payable", status=status)
        cursor.execute("SELECT invoice_id FROM api_credit_invoices WHERE tx_hash=%s AND invoice_id<>%s", (tx_hash, invoice_id))
        if cursor.fetchone():
            raise CommercialLaunchError("tx_hash_not_unique")
        cursor.execute(
            """UPDATE api_credit_invoices SET status='payment_detected', tx_hash=%s,
            source_address=%s, payment_detected_at=COALESCE(payment_detected_at,NOW()),
            last_checked_at=NOW(), updated_at=NOW(), last_error=NULL WHERE invoice_id=%s RETURNING *""",
            (tx_hash, source, invoice_id),
        )
        detected = _row(cursor, cursor.fetchone()) or {}
        _payment_event(cursor, invoice_id=invoice_id, event_type="payment.detected", actor="system:commercial-worker", from_status=status, to_status="payment_detected", reference=tx_hash, idempotency_key=f"{invoice_id}:detected:{tx_hash}")
        cursor.execute("UPDATE api_credit_invoices SET status='paid', paid_at=COALESCE(paid_at,NOW()), updated_at=NOW() WHERE invoice_id=%s RETURNING *", (invoice_id,))
        paid = _row(cursor, cursor.fetchone()) or detected
        _payment_event(cursor, invoice_id=invoice_id, event_type="payment.verified", actor="system:commercial-worker", from_status="payment_detected", to_status="paid", reference=tx_hash, idempotency_key=f"{invoice_id}:verified:{tx_hash}")
        result = _credit_locked_invoice(cursor, paid, actor="system:commercial-worker", reference=tx_hash)
        conn.commit()
        return {"ok": True, "idempotent": result["idempotent"], "invoice": _invoice_public(result["invoice"]), "balance_after": result["balance_after"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _load_payable_by_reference(references: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    ensure_commercial_launch_tables()
    unique = [item for item in dict.fromkeys(str(value) for value in references if str(value).startswith("api_pay_"))][:500]
    if not unique:
        return {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_invoices WHERE payment_reference=ANY(%s) AND status=ANY(%s) AND payment_provider='ton_treasury'", (unique, list(PAYABLE_INVOICE_STATES)))
        return {str(item.get("payment_reference") or ""): item for item in _rows(cursor, cursor.fetchall())}
    finally:
        cursor.close()
        conn.close()


def expire_open_invoices() -> int:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """UPDATE api_credit_invoices SET status='expired', updated_at=NOW(),
            last_error=COALESCE(last_error,'invoice_expired')
            WHERE status IN ('pending','awaiting_payment') AND expires_at<NOW() RETURNING invoice_id, status"""
        )
        expired = _rows(cursor, cursor.fetchall())
        for item in expired:
            _payment_event(cursor, invoice_id=str(item.get("invoice_id") or ""), event_type="invoice.expired", actor="system:commercial-worker", from_status="awaiting_payment", to_status="expired", idempotency_key=f"{item.get('invoice_id')}:expired")
        conn.commit()
        return len(expired)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def install_worker_patch() -> None:
    ensure_commercial_launch_tables()
    legacy._load_payable_invoices_by_reference = _load_payable_by_reference
    legacy._settle_invoice_from_tx = settle_invoice_from_ton
    legacy.expire_pending_invoices = expire_open_invoices


def scan_payments_once(*, page_limit: int = 100, max_pages: int = 5) -> Dict[str, Any]:
    install_worker_patch()
    return legacy.scan_commercial_payments_once(page_limit=page_limit, max_pages=max_pages)


def run_commercial_worker_forever() -> None:
    install_worker_patch()
    legacy.run_commercial_worker_forever()


def refresh_owned_invoice(*, user_id: int, invoice_id: str) -> Dict[str, Any]:
    invoice = get_owned_invoice(user_id=int(user_id), invoice_id=str(invoice_id))
    if not invoice:
        raise CommercialLaunchError("invoice_not_found")
    if invoice.get("payment_provider") == "ton_treasury":
        scan = scan_payments_once(page_limit=100, max_pages=3)
    else:
        scan = {"ok": False, "error": "manual_review_required"}
    refreshed = get_owned_invoice(user_id=int(user_id), invoice_id=str(invoice_id))
    return {"invoice": refreshed, "scan": scan}


def request_live_access(
    *, user_id: int, client_id: int, company_name: str, website: str, use_case: str,
    expected_monthly_requests: int, contact: str,
) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    if not legacy.commercial_launch_enabled():
        raise CommercialLaunchError("commercial_launch_disabled")
    company = _clean_text(company_name, minimum=2, maximum=160, code="company_name_required")
    site = _validate_website(website)
    case = _clean_text(use_case, minimum=20, maximum=2000, code="live_use_case_required")
    contact_value = _clean_text(contact, minimum=2, maximum=160, code="contact_required")
    volume = int(expected_monthly_requests)
    if volume < 0 or volume > 100_000_000:
        raise CommercialLaunchError("invalid_expected_monthly_requests")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise CommercialLaunchError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise CommercialLaunchError("project_not_active")
        if str(client.get("commercial_status") or "test_only") == "live_approved":
            raise CommercialLaunchError("live_access_already_approved")
        cursor.execute("SELECT * FROM api_live_access_requests WHERE client_id=%s AND status='live_requested' ORDER BY id DESC LIMIT 1 FOR UPDATE", (int(client_id),))
        existing = _row(cursor, cursor.fetchone())
        if existing:
            conn.commit()
            return {**existing, "idempotent": True}
        request_id = f"live_req_{secrets.token_hex(18)}"
        cursor.execute(
            """INSERT INTO api_live_access_requests (
                request_id,user_id,client_id,status,company_name,website,use_case,
                expected_monthly_requests,contact,terms_version
            ) VALUES (%s,%s,%s,'live_requested',%s,%s,%s,%s,%s,'2026-07') RETURNING *""",
            (request_id, int(user_id), int(client_id), company, site, case, volume, contact_value),
        )
        request = _row(cursor, cursor.fetchone()) or {}
        cursor.execute("UPDATE api_clients SET commercial_status='live_requested', live_keys_enabled=FALSE, updated_at=NOW() WHERE id=%s", (int(client_id),))
        _audit(cursor, f"user:{int(user_id)}", "commercial.live.request", "api_client", client_id, {
            "request_id": request_id, "company_name": company, "website": site,
            "expected_monthly_requests": volume, "contact": contact_value,
        })
        conn.commit()
        return {**request, "idempotent": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def review_live_access(*, client_id: int, action: str, actor: str, comment: str = "") -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    action_value = str(action or "").strip().lower()
    if action_value not in {"approve", "reject", "suspend"}:
        raise CommercialLaunchError("invalid_live_action")
    note = _optional_text(comment, 1000)
    if action_value == "reject" and not note:
        raise CommercialLaunchError("rejection_reason_required")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (int(client_id),))
        client = _row(cursor, cursor.fetchone())
        if not client:
            raise CommercialLaunchError("project_not_found")
        cursor.execute("SELECT * FROM api_live_access_requests WHERE client_id=%s ORDER BY id DESC LIMIT 1 FOR UPDATE", (int(client_id),))
        request = _row(cursor, cursor.fetchone())
        current = str(client.get("commercial_status") or "test_only")
        if action_value == "suspend":
            if current != "live_approved":
                raise CommercialLaunchError("live_access_not_approved")
            next_state = "live_suspended"
        elif action_value == "approve":
            if not request or str(request.get("status") or "") not in {"live_requested", "live_rejected", "live_suspended"}:
                raise CommercialLaunchError("live_access_request_not_found")
            next_state = "live_approved"
        else:
            if not request or str(request.get("status") or "") != "live_requested":
                raise CommercialLaunchError("live_access_request_not_found")
            next_state = "live_rejected"
        if request:
            cursor.execute(
                """UPDATE api_live_access_requests SET status=%s, reviewed_at=NOW(), reviewed_by=%s,
                review_note=%s, admin_comment=%s, updated_at=NOW() WHERE id=%s RETURNING *""",
                (next_state, str(actor)[:100], note, note, int(request.get("id") or 0)),
            )
            request = _row(cursor, cursor.fetchone()) or request
        cursor.execute(
            """UPDATE api_clients SET commercial_status=%s, live_keys_enabled=%s, updated_at=NOW()
            WHERE id=%s RETURNING *""",
            (next_state, next_state == "live_approved", int(client_id)),
        )
        updated = _row(cursor, cursor.fetchone()) or {}
        _audit(cursor, actor, f"commercial.live.{action_value}", "api_client", client_id, {"state": next_state, "comment": note, "request_id": request.get("request_id") if request else None})
        conn.commit()
        return {"request": request, "client": updated}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def issue_live_key(*, user_id: int, client_id: int, name: str, scopes: Optional[Iterable[str]]) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    if not legacy.live_keys_globally_enabled():
        raise CommercialLaunchError("live_keys_disabled")
    scope_list = _normalize_live_scopes(scopes)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise CommercialLaunchError("project_not_found")
        if str(client.get("status") or "") != "active":
            raise CommercialLaunchError("project_not_active")
        if str(client.get("commercial_status") or "") != "live_approved" or not bool(client.get("live_keys_enabled")):
            raise CommercialLaunchError("live_access_not_approved")
        minimum = live_minimum_balance()
        balance = int(client.get("credit_balance") or 0)
        if minimum > 0 and balance < minimum:
            raise CommercialLaunchError("live_minimum_balance_required", minimum_balance=minimum, balance=balance)
        cursor.execute("SELECT COUNT(*) FROM api_keys WHERE client_id=%s AND status='active'", (int(client_id),))
        row = cursor.fetchone()
        count = int((row[0] if not isinstance(row, dict) else next(iter(row.values()))) or 0)
        if count >= max_keys_per_project():
            raise CommercialLaunchError("key_limit_reached", limit=max_keys_per_project())
        raw_key, key_prefix, key_hash = generate_api_key("live")
        cursor.execute(
            """INSERT INTO api_keys (client_id,name,environment,key_hash,key_prefix,scopes,status)
            VALUES (%s,%s,'live',%s,%s,%s,'active')
            RETURNING id,client_id,name,environment,key_prefix,scopes,status,created_at""",
            (int(client_id), _clean_key_name(name), key_hash, key_prefix, ",".join(scope_list)),
        )
        key = _row(cursor, cursor.fetchone()) or {}
        _audit(cursor, f"user:{int(user_id)}", "commercial.live_key.issue", "api_key", key.get("id"), {"client_id": int(client_id), "scopes": scope_list, "environment": "live"})
        conn.commit()
        return {**key, "raw_key": raw_key, "scopes": scope_list}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def set_billing_controls(
    *, user_id: int, client_id: int, low_balance_threshold: Optional[int],
    max_daily_credit_spend: Optional[int], max_monthly_credit_spend: Optional[int],
    auto_recharge_enabled: bool = False, auto_recharge_package_code: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    if auto_recharge_enabled:
        raise CommercialLaunchError("auto_recharge_unavailable")
    values = {
        "low_balance_threshold": low_balance_threshold,
        "daily_spend_limit_credits": max_daily_credit_spend,
        "monthly_spend_limit_credits": max_monthly_credit_spend,
    }
    for name, value in values.items():
        if value is not None and (int(value) < 0 or int(value) > 1_000_000_000):
            raise CommercialLaunchError(f"invalid_{name}")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = _owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise CommercialLaunchError("project_not_found")
        cursor.execute(
            """UPDATE api_clients SET low_balance_threshold=%s, daily_spend_limit_credits=%s,
            monthly_spend_limit_credits=%s, auto_recharge_enabled=FALSE,
            auto_recharge_package_code=%s, updated_at=NOW() WHERE id=%s RETURNING *""",
            (low_balance_threshold, max_daily_credit_spend, max_monthly_credit_spend, auto_recharge_package_code, int(client_id)),
        )
        updated = _row(cursor, cursor.fetchone()) or {}
        _audit(cursor, f"user:{int(user_id)}", "commercial.billing_controls.update", "api_client", client_id, {
            "low_balance_threshold": low_balance_threshold,
            "max_daily_credit_spend": max_daily_credit_spend,
            "max_monthly_credit_spend": max_monthly_credit_spend,
            "auto_recharge_enabled": False,
        })
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def spend_snapshot(client_id: int, cursor=None) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    own = cursor is None
    conn = get_connection() if own else None
    cur = conn.cursor() if own else cursor
    try:
        cur.execute(
            """SELECT credit_balance,low_balance_threshold,daily_spend_limit_credits,
            monthly_spend_limit_credits,auto_recharge_enabled,auto_recharge_package_code,
            last_low_balance_notified_at,last_low_balance_notified_balance
            FROM api_clients WHERE id=%s""",
            (int(client_id),),
        )
        client = _row(cur, cur.fetchone()) or {}
        cur.execute(
            """SELECT
            COALESCE(SUM(units) FILTER (WHERE created_at>=date_trunc('day',NOW())),0) AS daily_used,
            COALESCE(SUM(units) FILTER (WHERE created_at>=date_trunc('month',NOW())),0) AS monthly_used
            FROM api_credit_reservations WHERE client_id=%s AND status IN ('reserved','charged')""",
            (int(client_id),),
        )
        used = _row(cur, cur.fetchone()) or {}
        cur.execute("SELECT product_code,price_credits FROM api_products WHERE product_code IN ('quick_analysis','opportunity_scan')")
        prices = {str(item.get("product_code")): int(item.get("price_credits") or 0) for item in _rows(cur, cur.fetchall())}
        balance = int(client.get("credit_balance") or 0)
        threshold = client.get("low_balance_threshold")
        threshold_value = int(threshold) if threshold is not None else None
        daily_limit = client.get("daily_spend_limit_credits")
        monthly_limit = client.get("monthly_spend_limit_credits")
        quick_price = max(1, int(prices.get("quick_analysis") or 10))
        opportunity_price = max(1, int(prices.get("opportunity_scan") or 1))
        return {
            "balance": balance,
            "low_balance_threshold": threshold_value,
            "low_balance": threshold_value is not None and balance <= threshold_value,
            "max_daily_credit_spend": int(daily_limit) if daily_limit is not None else None,
            "max_monthly_credit_spend": int(monthly_limit) if monthly_limit is not None else None,
            "daily_spend": int(used.get("daily_used") or 0),
            "monthly_spend": int(used.get("monthly_used") or 0),
            "estimated_remaining_quick_analyses": balance // quick_price,
            "estimated_remaining_opportunity_scans": balance // opportunity_price,
            "auto_recharge_enabled": False,
            "auto_recharge_package_code": client.get("auto_recharge_package_code"),
            "last_low_balance_notified_at": client.get("last_low_balance_notified_at"),
            "last_low_balance_notified_balance": client.get("last_low_balance_notified_balance"),
        }
    finally:
        if own and conn:
            cur.close()
            conn.close()


def commercial_overview(user_id: int) -> Dict[str, Any]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT c.id,c.name,c.status,c.credit_balance,c.commercial_status,c.live_keys_enabled,
            c.low_balance_threshold,c.daily_spend_limit_credits,c.monthly_spend_limit_credits,
            (SELECT COUNT(*) FROM api_keys k WHERE k.client_id=c.id AND k.status='active' AND k.environment='live') AS active_live_keys
            FROM api_client_owners o JOIN api_clients c ON c.id=o.client_id
            WHERE o.user_id=%s ORDER BY c.id DESC""",
            (int(user_id),),
        )
        projects = _rows(cursor, cursor.fetchall())
        for project in projects:
            project["spend"] = spend_snapshot(int(project.get("id") or 0), cursor=cursor)
            cursor.execute("SELECT * FROM api_live_access_requests WHERE client_id=%s ORDER BY id DESC LIMIT 1", (int(project.get("id") or 0),))
            request = _row(cursor, cursor.fetchone())
            if request:
                request.pop("reviewed_by", None)
            project["live_access_request"] = request
        return {
            "enabled": legacy.commercial_launch_enabled(),
            "credit_purchases_enabled": credit_purchases_enabled(),
            "live_keys_enabled": legacy.live_keys_globally_enabled(),
            "payment_provider": invoice_provider_name(),
            "automatic_payment_verification": invoice_provider_name() == "ton_treasury",
            "configured_currency": _configured_currency(),
            "treasury_incoming_enabled": incoming_enabled(),
            "network": legacy._runtime_network(),
            "packages": list_credit_packages(include_disabled=False),
            "projects": projects,
            "invoices": list_owned_invoices(user_id=int(user_id), limit=100),
        }
    finally:
        cursor.close()
        conn.close()


def list_live_requests(*, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """SELECT r.*,c.name AS client_name,c.credit_balance,c.commercial_status,c.live_keys_enabled
            FROM api_live_access_requests r JOIN api_clients c ON c.id=r.client_id
            ORDER BY CASE r.status WHEN 'live_requested' THEN 0 ELSE 1 END,r.id DESC LIMIT %s""",
            (max(1, min(int(limit), 1000)),),
        )
        return _rows(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def list_all_invoices(*, status: str = "", client_id: Optional[int] = None, provider: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    clauses = []
    params: List[Any] = []
    if status:
        clauses.append("i.status=%s")
        params.append(status)
    if client_id:
        clauses.append("i.client_id=%s")
        params.append(int(client_id))
    if provider:
        clauses.append("i.payment_provider=%s")
        params.append(provider)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT i.*,c.name AS client_name FROM api_credit_invoices i JOIN api_clients c ON c.id=i.client_id {where} ORDER BY i.id DESC LIMIT %s", tuple(params))
        result = []
        for item in _rows(cursor, cursor.fetchall()):
            public = _invoice_public(item)
            public["client_name"] = item.get("client_name")
            public["owner_user_id"] = int(item.get("owner_user_id") or item.get("user_id") or 0)
            result.append(public)
        return result
    finally:
        cursor.close()
        conn.close()


def list_recent_purchase_ledger(*, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_credit_ledger WHERE event_type='purchase' ORDER BY id DESC LIMIT %s", (max(1, min(int(limit), 500)),))
        return _rows(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()


def list_payment_events(*, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_commercial_launch_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_payment_events ORDER BY id DESC LIMIT %s", (max(1, min(int(limit), 1000)),))
        return _rows(cursor, cursor.fetchall())
    finally:
        cursor.close()
        conn.close()
