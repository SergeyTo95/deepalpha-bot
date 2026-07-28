"""Security and migration hardening for the commercial launch service.

This module wraps the first commercial-launch implementation so startup remains fail-closed,
database initialization stays inside the guarded WebApp startup block, and compatibility fixes
can be applied without weakening the existing TON settlement contour.
"""

import json
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

from db.database import get_connection
from services import developer_api_commercial_launch_service as base
from services import developer_api_commercial_service as legacy
from services.developer_api_billing_service import normalize_idempotency_key


CommercialLaunchError = base.CommercialLaunchError
PaymentProviderAdapter = base.PaymentProviderAdapter
ManualPaymentAdapter = base.ManualPaymentAdapter
TonTreasuryPaymentAdapter = base.TonTreasuryPaymentAdapter
LIVE_STATES = base.LIVE_STATES
INVOICE_STATES = base.INVOICE_STATES
OPEN_INVOICE_STATES = base.OPEN_INVOICE_STATES
PAYABLE_INVOICE_STATES = base.PAYABLE_INVOICE_STATES
LIVE_ALLOWED_SCOPES = base.LIVE_ALLOWED_SCOPES
UNSET = object()

_V2_TABLES_READY = False
_ORIGINAL_ENSURE = base.ensure_commercial_launch_tables
_ORIGINAL_CREATE_INVOICE = base.create_credit_invoice
_ORIGINAL_INVOICE_PUBLIC = base._invoice_public


def credit_purchases_enabled() -> bool:
    """Remain closed unless both commercial launch and purchase gates are explicit."""
    return legacy.commercial_launch_enabled() and base._env_true(
        "API_CREDIT_PURCHASES_ENABLED",
        False,
    )


def _seed_configured_packages(cursor) -> int:
    raw = str(os.getenv("API_CREDIT_PACKAGES_JSON", "") or "").strip()
    if not raw:
        return 0
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        raise CommercialLaunchError("invalid_api_credit_packages_json") from exc
    packages = parsed if isinstance(parsed, list) else [parsed]
    inserted = 0
    for item in packages:
        if not isinstance(item, dict):
            raise CommercialLaunchError("invalid_api_credit_package")
        code = legacy._clean_package_code(item.get("package_code"))
        name = legacy._clean_package_name(item.get("display_name") or code)
        credits = int(item.get("credits") or 0)
        if credits <= 0 or credits > 1_000_000_000:
            raise CommercialLaunchError("invalid_package_credits")
        currency = str(item.get("price_currency") or base._configured_currency()).strip().upper()
        if currency != base._configured_currency():
            raise CommercialLaunchError(
                "unsupported_launch_currency",
                configured_currency=base._configured_currency(),
            )
        price_amount = item.get("price_amount")
        if price_amount in (None, "") and item.get("price_nano") not in (None, ""):
            price_amount = Decimal(int(item.get("price_nano"))) / Decimal(1_000_000_000)
        amount = base._decimal(price_amount)
        price_nano = base._ton_to_nano(amount) if currency == "TON" else 1
        metadata = item.get("metadata_json", item.get("metadata"))
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        cursor.execute(
            """
            INSERT INTO api_credit_packages (
                package_code, display_name, credits, price_nano, price_amount,
                price_currency, enabled, sort_order, metadata_json
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (package_code) DO NOTHING
            RETURNING id
            """,
            (
                code,
                name,
                credits,
                price_nano,
                str(amount),
                currency,
                bool(item.get("enabled", True)),
                int(item.get("sort_order") or 0),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        if cursor.fetchone():
            inserted += 1
    return inserted


def ensure_commercial_launch_tables() -> None:
    """Run v1 migrations first, then seed the documented v2 package schema."""
    global _V2_TABLES_READY
    if _V2_TABLES_READY:
        return
    _ORIGINAL_ENSURE()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        _seed_configured_packages(cursor)
        conn.commit()
        _V2_TABLES_READY = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _invoice_public(item: Dict[str, Any]) -> Dict[str, Any]:
    """Never expose the legacy manual-provider treasury sentinel as a payment address."""
    result = _ORIGINAL_INVOICE_PUBLIC(item)
    if str(result.get("payment_provider") or "") == "manual" and not item.get("payment_address"):
        result["payment_address"] = None
        result["checkout_url"] = None
    return result


def create_credit_invoice(
    *,
    user_id: int,
    client_id: int,
    package_code: str,
    idempotency_key: str,
    provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Accept both pre-provider and provider-aware fingerprints on idempotent replay."""
    try:
        return _ORIGINAL_CREATE_INVOICE(
            user_id=user_id,
            client_id=client_id,
            package_code=package_code,
            idempotency_key=idempotency_key,
            provider=provider,
        )
    except CommercialLaunchError as exc:
        if exc.code != "idempotency_conflict":
            raise
    key = normalize_idempotency_key(idempotency_key)
    code = legacy._clean_package_code(package_code)
    requested_provider = base.payment_adapter(provider).name
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT i.* FROM api_credit_invoices i
            JOIN api_client_owners o ON o.client_id=i.client_id
            WHERE i.user_id=%s AND o.user_id=%s AND i.idempotency_key=%s
            FOR UPDATE OF i
            """,
            (int(user_id), int(user_id), key),
        )
        existing = base._row(cursor, cursor.fetchone())
        if not existing:
            raise CommercialLaunchError("idempotency_conflict")
        old_fingerprint = legacy._canonical_fingerprint(
            {"client_id": int(client_id), "package_code": code}
        )
        stored_provider = str(existing.get("payment_provider") or "ton_treasury")
        if (
            str(existing.get("request_fingerprint") or "") != old_fingerprint
            or int(existing.get("client_id") or 0) != int(client_id)
            or str(existing.get("package_code") or "") != code
            or stored_provider != requested_provider
        ):
            raise CommercialLaunchError("idempotency_conflict")
        conn.commit()
        return {**_invoice_public(existing), "idempotent": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def review_live_access(
    *, client_id: int, action: str, actor: str, comment: str = ""
) -> Dict[str, Any]:
    """Review live access and revoke all active live keys atomically on suspension."""
    ensure_commercial_launch_tables()
    action_value = str(action or "").strip().lower()
    if action_value not in {"approve", "reject", "suspend"}:
        raise CommercialLaunchError("invalid_live_action")
    note = base._optional_text(comment, 1000)
    if action_value == "reject" and not note:
        raise CommercialLaunchError("rejection_reason_required")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM api_clients WHERE id=%s FOR UPDATE", (int(client_id),))
        client = base._row(cursor, cursor.fetchone())
        if not client:
            raise CommercialLaunchError("project_not_found")
        cursor.execute(
            """
            SELECT * FROM api_live_access_requests
            WHERE client_id=%s ORDER BY id DESC LIMIT 1 FOR UPDATE
            """,
            (int(client_id),),
        )
        request = base._row(cursor, cursor.fetchone())
        current = str(client.get("commercial_status") or "test_only")
        if action_value == "suspend":
            if current != "live_approved":
                raise CommercialLaunchError("live_access_not_approved")
            next_state = "live_suspended"
        elif action_value == "approve":
            if not request or str(request.get("status") or "") not in {
                "live_requested",
                "live_rejected",
                "live_suspended",
            }:
                raise CommercialLaunchError("live_access_request_not_found")
            next_state = "live_approved"
        else:
            if not request or str(request.get("status") or "") != "live_requested":
                raise CommercialLaunchError("live_access_request_not_found")
            next_state = "live_rejected"
        if request:
            cursor.execute(
                """
                UPDATE api_live_access_requests
                SET status=%s, reviewed_at=NOW(), reviewed_by=%s,
                    review_note=%s, admin_comment=%s, updated_at=NOW()
                WHERE id=%s RETURNING *
                """,
                (
                    next_state,
                    str(actor)[:100],
                    note,
                    note,
                    int(request.get("id") or 0),
                ),
            )
            request = base._row(cursor, cursor.fetchone()) or request
        revoked_live_key_ids: List[int] = []
        if next_state == "live_suspended":
            cursor.execute(
                """
                UPDATE api_keys
                SET status='revoked', revoked_at=COALESCE(revoked_at,NOW())
                WHERE client_id=%s AND environment='live' AND status='active'
                RETURNING id
                """,
                (int(client_id),),
            )
            revoked_live_key_ids = [
                int(row.get("id") if isinstance(row, dict) else row[0])
                for row in (cursor.fetchall() or [])
            ]
        cursor.execute(
            """
            UPDATE api_clients
            SET commercial_status=%s, live_keys_enabled=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
            """,
            (next_state, next_state == "live_approved", int(client_id)),
        )
        updated = base._row(cursor, cursor.fetchone()) or {}
        base._audit(
            cursor,
            actor,
            f"commercial.live.{action_value}",
            "api_client",
            client_id,
            {
                "state": next_state,
                "comment": note,
                "request_id": request.get("request_id") if request else None,
                "revoked_live_key_ids": revoked_live_key_ids,
            },
        )
        conn.commit()
        return {
            "request": request,
            "client": updated,
            "revoked_live_key_ids": revoked_live_key_ids,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _control_value(value: Any, current: Any, field: str) -> Any:
    if value is UNSET:
        return current
    if value is None:
        return None
    try:
        normalized = int(value)
    except Exception as exc:
        raise CommercialLaunchError(f"invalid_{field}") from exc
    if normalized < 0 or normalized > 1_000_000_000:
        raise CommercialLaunchError(f"invalid_{field}")
    return normalized


def set_billing_controls(
    *,
    user_id: int,
    client_id: int,
    low_balance_threshold: Any = UNSET,
    max_daily_credit_spend: Any = UNSET,
    max_monthly_credit_spend: Any = UNSET,
    auto_recharge_enabled: Any = UNSET,
    auto_recharge_package_code: Any = UNSET,
) -> Dict[str, Any]:
    """Preserve omitted controls while allowing an explicit JSON null to disable a limit."""
    ensure_commercial_launch_tables()
    if auto_recharge_enabled is not UNSET and bool(auto_recharge_enabled):
        raise CommercialLaunchError("auto_recharge_unavailable")
    conn = get_connection()
    cursor = conn.cursor()
    try:
        client = base._owned_client(cursor, int(user_id), int(client_id), for_update=True)
        if not client:
            raise CommercialLaunchError("project_not_found")
        low = _control_value(
            low_balance_threshold,
            client.get("low_balance_threshold"),
            "low_balance_threshold",
        )
        daily = _control_value(
            max_daily_credit_spend,
            client.get("daily_spend_limit_credits"),
            "daily_spend_limit_credits",
        )
        monthly = _control_value(
            max_monthly_credit_spend,
            client.get("monthly_spend_limit_credits"),
            "monthly_spend_limit_credits",
        )
        package_code = (
            client.get("auto_recharge_package_code")
            if auto_recharge_package_code is UNSET
            else (str(auto_recharge_package_code or "").strip() or None)
        )
        cursor.execute(
            """
            UPDATE api_clients
            SET low_balance_threshold=%s, daily_spend_limit_credits=%s,
                monthly_spend_limit_credits=%s, auto_recharge_enabled=FALSE,
                auto_recharge_package_code=%s, updated_at=NOW()
            WHERE id=%s RETURNING *
            """,
            (low, daily, monthly, package_code, int(client_id)),
        )
        updated = base._row(cursor, cursor.fetchone()) or {}
        base._audit(
            cursor,
            f"user:{int(user_id)}",
            "commercial.billing_controls.update",
            "api_client",
            client_id,
            {
                "low_balance_threshold": low,
                "max_daily_credit_spend": daily,
                "max_monthly_credit_spend": monthly,
                "auto_recharge_enabled": False,
            },
        )
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def spend_snapshot(client_id: int, cursor=None) -> Dict[str, Any]:
    """Return spend and estimates using the real api_products.unit_price schema."""
    ensure_commercial_launch_tables()
    own = cursor is None
    conn = get_connection() if own else None
    cur = conn.cursor() if own else cursor
    try:
        cur.execute(
            """
            SELECT credit_balance,low_balance_threshold,daily_spend_limit_credits,
                   monthly_spend_limit_credits,auto_recharge_enabled,
                   auto_recharge_package_code,last_low_balance_notified_at,
                   last_low_balance_notified_balance
            FROM api_clients WHERE id=%s
            """,
            (int(client_id),),
        )
        client = base._row(cur, cur.fetchone()) or {}
        cur.execute(
            """
            SELECT
              COALESCE(SUM(units) FILTER (WHERE created_at>=date_trunc('day',NOW())),0) AS daily_used,
              COALESCE(SUM(units) FILTER (WHERE created_at>=date_trunc('month',NOW())),0) AS monthly_used
            FROM api_credit_reservations
            WHERE client_id=%s AND status IN ('reserved','charged')
            """,
            (int(client_id),),
        )
        used = base._row(cur, cur.fetchone()) or {}
        cur.execute(
            """
            SELECT product_code, unit_price
            FROM api_products
            WHERE product_code IN ('quick_analysis','opportunity_scan')
            """
        )
        prices = {
            str(item.get("product_code")): int(item.get("unit_price") or 0)
            for item in base._rows(cur, cur.fetchall())
        }
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


# Patch globals used dynamically inside functions defined by the base module.
base.credit_purchases_enabled = credit_purchases_enabled
base.ensure_commercial_launch_tables = ensure_commercial_launch_tables
base._invoice_public = _invoice_public
base.create_credit_invoice = create_credit_invoice
base.review_live_access = review_live_access
base.set_billing_controls = set_billing_controls
base.spend_snapshot = spend_snapshot

# Re-export unchanged public surface used by routes, workers and tests.
payment_adapter = base.payment_adapter
invoice_provider_name = base.invoice_provider_name
invoice_ttl_hours = base.invoice_ttl_hours
maximum_open_invoices = base.maximum_open_invoices
live_minimum_balance = base.live_minimum_balance
list_credit_packages = base.list_credit_packages
upsert_credit_package = base.upsert_credit_package
list_owned_invoices = base.list_owned_invoices
get_owned_invoice = base.get_owned_invoice
cancel_owned_invoice = base.cancel_owned_invoice
mark_invoice_paid_admin = base.mark_invoice_paid_admin
credit_invoice_admin = base.credit_invoice_admin
cancel_invoice_admin = base.cancel_invoice_admin
settle_invoice_from_ton = base.settle_invoice_from_ton
expire_open_invoices = base.expire_open_invoices
install_worker_patch = base.install_worker_patch
scan_payments_once = base.scan_payments_once
run_commercial_worker_forever = base.run_commercial_worker_forever
refresh_owned_invoice = base.refresh_owned_invoice
request_live_access = base.request_live_access
issue_live_key = base.issue_live_key
commercial_overview = base.commercial_overview
list_live_requests = base.list_live_requests
list_all_invoices = base.list_all_invoices
list_recent_purchase_ledger = base.list_recent_purchase_ledger
list_payment_events = base.list_payment_events
