import logging
import os
import secrets
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import requests

from db.database import get_active_cashier_payment_wallet, get_connection
from services.ton_chain_service import (
    get_ton_balance,
    get_toncenter_configuration_status,
    get_wallet_seqno,
    nano_to_ton_display,
    normalize_ton_address,
    resolve_recent_ton_tx_hash,
    send_boc_return_hash,
    validate_ton_address,
)
from services.ton_wallet_service import (
    _build_signed_transfer_message,
    _extract_boc_from_transfer,
    _wallet_from_mnemonic,
    decrypt_secret,
    get_user_ton_wallet,
)

try:
    from tonsdk.boc import Cell
    from tonsdk.utils import Address
except Exception:  # pragma: no cover - exercised by runtime readiness gate
    Cell = None
    Address = None


logger = logging.getLogger(__name__)

CANONICAL_USDT_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
USDT_DECIMALS = 6
GRAM_DECIMALS = 9

# Based on the standard TEP-74 transfer shape used by TON documentation.
# 0.1 Gram is attached to the sender Jetton wallet; 0.02 Gram is forwarded
# so the receiver gets a standards-compliant transfer_notification.
USDT_TRANSFER_VALUE_NANO = 100_000_000
USDT_FORWARD_NANO = 20_000_000
GRAM_FEE_RESERVE_NANO = 50_000_000
WITHDRAW_PREVIEW_TTL_SECONDS = 10 * 60
MAX_MEMO_BYTES = 120

WITHDRAW_GATE_ENV = "VELIA_GRAM_TREASURY_WITHDRAW_ENABLED"
USDT_WITHDRAW_GATE_ENV = "VELIA_GRAM_TREASURY_USDT_WITHDRAW_ENABLED"


class TreasuryWithdrawError(RuntimeError):
    pass


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "false") or "false").strip().lower() in {"1", "true", "yes", "on"}


def treasury_withdrawal_enabled() -> bool:
    return _env_true(WITHDRAW_GATE_ENV)


def treasury_usdt_withdrawal_enabled() -> bool:
    return treasury_withdrawal_enabled() and _env_true(USDT_WITHDRAW_GATE_ENV)


def _network() -> str:
    return str(os.getenv("TON_NETWORK", "testnet") or "testnet").strip().lower()


def _toncenter_api_key() -> str:
    if _network() == "mainnet":
        return str(os.getenv("TONCENTER_MAINNET_API_KEY") or os.getenv("TONCENTER_API_KEY") or "").strip()
    return str(os.getenv("TONCENTER_TESTNET_API_KEY") or os.getenv("TONCENTER_API_KEY") or "").strip()


def _toncenter_v3_base_url() -> str:
    override = str(os.getenv("TONCENTER_BASE_URL") or "").strip().rstrip("/")
    if override:
        if override.endswith("/api/v2"):
            return override[:-len("/api/v2")] + "/api/v3"
        if override.endswith("/api/v3"):
            return override
        return override + "/api/v3"
    if _network() == "mainnet":
        return "https://toncenter.com/api/v3"
    return "https://testnet.toncenter.com/api/v3"


def _toncenter_headers() -> Dict[str, str]:
    key = _toncenter_api_key()
    return {"X-API-Key": key} if key else {}


def _normalize_address(value: str) -> str:
    return normalize_ton_address(str(value or "").strip())


def _same_address(left: str, right: str) -> bool:
    a = _normalize_address(left)
    b = _normalize_address(right)
    return bool(a and b and a == b)


def _safe_memo(value: str) -> str:
    memo = str(value or "").strip()
    raw = memo.encode("utf-8")
    if len(raw) > MAX_MEMO_BYTES:
        raise TreasuryWithdrawError("memo_too_long")
    return memo


def usdt_to_raw(value: str) -> int:
    raw = str(value or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise TreasuryWithdrawError("invalid_amount")
    if not amount.is_finite() or amount <= 0:
        raise TreasuryWithdrawError("invalid_amount")
    scaled = amount * (Decimal(10) ** USDT_DECIMALS)
    if scaled != scaled.to_integral_value():
        raise TreasuryWithdrawError("too_many_decimals")
    result = int(scaled)
    if result <= 0:
        raise TreasuryWithdrawError("invalid_amount")
    return result


def gram_to_raw(value: str) -> int:
    raw = str(value or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise TreasuryWithdrawError("invalid_amount")
    if not amount.is_finite() or amount <= 0:
        raise TreasuryWithdrawError("invalid_amount")
    scaled = amount * (Decimal(10) ** GRAM_DECIMALS)
    if scaled != scaled.to_integral_value():
        raise TreasuryWithdrawError("too_many_decimals")
    result = int(scaled)
    if result <= 0:
        raise TreasuryWithdrawError("invalid_amount")
    return result


def format_usdt_raw(amount_raw: int) -> str:
    value = Decimal(int(amount_raw)) / (Decimal(10) ** USDT_DECIMALS)
    return format(value, "f").rstrip("0").rstrip(".") or "0"


def format_gram_raw(amount_raw: int) -> str:
    return nano_to_ton_display(int(amount_raw))


def format_asset_amount(asset: str, amount_raw: int) -> str:
    return format_usdt_raw(amount_raw) if str(asset).lower() == "usdt" else format_gram_raw(amount_raw)


def _ensure_withdrawal_table() -> None:
    """Idempotent narrow schema bootstrap for the admin-only withdrawal journal."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS gram_treasury_withdrawals (
                id BIGSERIAL PRIMARY KEY,
                reference TEXT NOT NULL UNIQUE,
                admin_user_id BIGINT NOT NULL,
                source_wallet_address TEXT NOT NULL,
                asset TEXT NOT NULL,
                jetton_master TEXT,
                amount_raw NUMERIC(78,0) NOT NULL,
                destination_address TEXT NOT NULL,
                memo TEXT,
                status TEXT NOT NULL DEFAULT 'prepared',
                tx_hash TEXT,
                query_id NUMERIC(78,0),
                error_code TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                confirmed_at TIMESTAMP,
                submitted_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_gram_treasury_withdrawals_status_created "
            "ON gram_treasury_withdrawals(status, created_at DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_gram_treasury_withdrawals_admin_created "
            "ON gram_treasury_withdrawals(admin_user_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def _resolve_treasury_source(admin_user_id: int) -> Dict[str, Any]:
    cashier = get_active_cashier_payment_wallet() or {}
    if not cashier or not cashier.get("wallet_address"):
        return {"ok": False, "error": "treasury_not_configured"}

    admin_wallet = get_user_ton_wallet(int(admin_user_id)) or {}
    if admin_wallet.get("wallet_conflict"):
        return {"ok": False, "error": "admin_wallet_conflict"}
    if not admin_wallet.get("wallet_address") or not admin_wallet.get("id"):
        return {"ok": False, "error": "admin_wallet_not_found"}
    if str(admin_wallet.get("status") or "").lower() != "active":
        return {"ok": False, "error": "admin_wallet_not_active"}

    source = _normalize_address(str(cashier.get("wallet_address") or ""))
    admin_source = _normalize_address(str(admin_wallet.get("wallet_address") or ""))
    if not validate_ton_address(source) or source != admin_source:
        return {"ok": False, "error": "treasury_signing_source_mismatch"}

    network = str(cashier.get("network") or admin_wallet.get("network") or "").strip().lower()
    if _network() != "mainnet" or "test" in network or network not in {"mainnet", "-239"}:
        return {"ok": False, "error": "treasury_requires_mainnet"}

    return {
        "ok": True,
        "source_address": source,
        "admin_wallet_id": int(admin_wallet["id"]),
        "cashier_wallet_id": cashier.get("id"),
        "cashier_mode": "managed" if cashier.get("seed_encrypted") else "watch-only",
    }


def _load_signing_seed(admin_user_id: int, expected_wallet_id: int, expected_source: str) -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id,wallet_address,network,status,seed_encrypted
            FROM user_ton_wallets
            WHERE user_id=%s
            ORDER BY id ASC
            """,
            (int(admin_user_id),),
        )
        rows = cur.fetchall() or []
    finally:
        conn.close()
    if len(rows) != 1:
        return {"ok": False, "error": "admin_wallet_conflict" if rows else "admin_wallet_not_found"}
    row = rows[0]
    if int(row[0]) != int(expected_wallet_id):
        return {"ok": False, "error": "wallet_selection_stale"}
    if str(row[3] or "").lower() != "active":
        return {"ok": False, "error": "admin_wallet_not_active"}
    if not _same_address(str(row[1] or ""), expected_source):
        return {"ok": False, "error": "treasury_signing_source_mismatch"}
    if not str(row[4] or "").strip():
        return {"ok": False, "error": "signing_seed_unavailable"}
    return {"ok": True, "seed_encrypted": str(row[4]), "source_address": _normalize_address(str(row[1]))}


def _fetch_usdt_wallet_state(owner_address: str) -> Dict[str, Any]:
    owner = _normalize_address(owner_address)
    if _network() != "mainnet":
        return {"ok": False, "error": "usdt_requires_mainnet"}
    if not validate_ton_address(owner):
        return {"ok": False, "error": "invalid_source_wallet"}
    try:
        response = requests.get(
            f"{_toncenter_v3_base_url()}/jetton/wallets",
            params={
                "owner_address": owner,
                "jetton_address": CANONICAL_USDT_MASTER,
                "exclude_zero_balance": "false",
                "limit": 10,
            },
            headers=_toncenter_headers(),
            timeout=20,
        )
        data = response.json() if response.ok else {}
    except Exception:
        return {"ok": False, "error": "usdt_balance_unavailable"}
    if not response.ok:
        return {"ok": False, "error": "usdt_balance_unavailable"}

    canonical_master = _normalize_address(CANONICAL_USDT_MASTER)
    matches = []
    for row in data.get("jetton_wallets") or []:
        if not isinstance(row, dict):
            continue
        row_owner = _normalize_address(str(row.get("owner") or ""))
        row_master = _normalize_address(str(row.get("jetton") or ""))
        if row_owner == owner and row_master == canonical_master:
            matches.append(row)
    if len(matches) > 1:
        return {"ok": False, "error": "usdt_wallet_conflict"}
    if not matches:
        return {
            "ok": True,
            "wallet_address": "",
            "owner_address": owner,
            "jetton_master": CANONICAL_USDT_MASTER,
            "balance_raw": 0,
            "balance_display": "0",
            "deployed": False,
        }

    row = matches[0]
    wallet_address = _normalize_address(str(row.get("address") or ""))
    if not validate_ton_address(wallet_address):
        return {"ok": False, "error": "invalid_usdt_wallet"}
    try:
        balance_raw = int(str(row.get("balance") or "0"))
    except Exception:
        return {"ok": False, "error": "invalid_usdt_balance"}
    if balance_raw < 0:
        return {"ok": False, "error": "invalid_usdt_balance"}
    return {
        "ok": True,
        "wallet_address": wallet_address,
        "owner_address": owner,
        "jetton_master": CANONICAL_USDT_MASTER,
        "balance_raw": balance_raw,
        "balance_display": format_usdt_raw(balance_raw),
        "deployed": True,
    }


def get_treasury_withdraw_snapshot(admin_user_id: int) -> Dict[str, Any]:
    _ensure_withdrawal_table()
    source = _resolve_treasury_source(admin_user_id)
    if not source.get("ok"):
        return {
            **source,
            "withdraw_enabled": treasury_withdrawal_enabled(),
            "usdt_withdraw_enabled": treasury_usdt_withdrawal_enabled(),
        }

    runtime = get_toncenter_configuration_status()
    runtime_ready = bool(
        _network() == "mainnet"
        and runtime.get("endpoint_available")
        and runtime.get("network_valid")
        and Cell is not None
        and Address is not None
    )
    gram_balance_raw: Optional[int]
    gram_error = ""
    try:
        gram_balance_raw = int(get_ton_balance(source["source_address"]))
    except Exception:
        gram_balance_raw = None
        gram_error = "gram_balance_unavailable"

    usdt = _fetch_usdt_wallet_state(source["source_address"])
    return {
        **source,
        "withdraw_enabled": treasury_withdrawal_enabled(),
        "usdt_withdraw_enabled": treasury_usdt_withdrawal_enabled(),
        "runtime_ready": runtime_ready,
        "toncenter_api_key_configured": bool(runtime.get("api_key_configured")),
        "gram_balance_raw": gram_balance_raw,
        "gram_balance_display": format_gram_raw(gram_balance_raw) if gram_balance_raw is not None else "—",
        "gram_error": gram_error,
        "usdt": usdt,
        "usdt_master": CANONICAL_USDT_MASTER,
        "usdt_decimals": USDT_DECIMALS,
        "usdt_transfer_value_nano": USDT_TRANSFER_VALUE_NANO,
        "usdt_forward_nano": USDT_FORWARD_NANO,
        "fee_reserve_nano": GRAM_FEE_RESERVE_NANO,
    }


def _validate_preflight(snapshot: Dict[str, Any], asset: str, amount_raw: int) -> Optional[str]:
    if not snapshot.get("ok"):
        return str(snapshot.get("error") or "treasury_unavailable")
    if not snapshot.get("runtime_ready"):
        return "treasury_runtime_not_ready"
    gram_raw = snapshot.get("gram_balance_raw")
    if gram_raw is None:
        return "gram_balance_unavailable"
    if asset == "gram":
        if int(gram_raw) < int(amount_raw) + GRAM_FEE_RESERVE_NANO:
            return "insufficient_gram_balance"
        return None
    if asset == "usdt":
        usdt = snapshot.get("usdt") or {}
        if not snapshot.get("usdt_withdraw_enabled"):
            return "usdt_withdraw_disabled"
        if not usdt.get("ok"):
            return str(usdt.get("error") or "usdt_balance_unavailable")
        if not usdt.get("wallet_address"):
            return "usdt_wallet_not_deployed"
        if int(usdt.get("balance_raw") or 0) < int(amount_raw):
            return "insufficient_usdt_balance"
        if int(gram_raw) < USDT_TRANSFER_VALUE_NANO + GRAM_FEE_RESERVE_NANO:
            return "insufficient_gram_for_usdt_gas"
        return None
    return "unsupported_asset"


def prepare_treasury_withdrawal(
    admin_user_id: int,
    asset: str,
    destination_address: str,
    amount_text: str,
    memo: str = "",
) -> Dict[str, Any]:
    _ensure_withdrawal_table()
    if not treasury_withdrawal_enabled():
        return {"ok": False, "error": "treasury_withdraw_disabled"}
    asset = str(asset or "").strip().lower()
    if asset not in {"gram", "usdt"}:
        return {"ok": False, "error": "unsupported_asset"}
    destination = _normalize_address(destination_address)
    if not validate_ton_address(destination):
        return {"ok": False, "error": "invalid_destination"}
    try:
        safe_memo = _safe_memo(memo)
        amount_raw = usdt_to_raw(amount_text) if asset == "usdt" else gram_to_raw(amount_text)
    except TreasuryWithdrawError as exc:
        return {"ok": False, "error": str(exc)}

    snapshot = get_treasury_withdraw_snapshot(admin_user_id)
    if snapshot.get("ok") and _same_address(snapshot.get("source_address", ""), destination):
        return {"ok": False, "error": "destination_is_treasury"}
    preflight_error = _validate_preflight(snapshot, asset, amount_raw)
    if preflight_error:
        return {"ok": False, "error": preflight_error, "snapshot": snapshot}

    reference = "GTW_" + secrets.token_hex(8)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO gram_treasury_withdrawals
                (reference,admin_user_id,source_wallet_address,asset,jetton_master,amount_raw,
                 destination_address,memo,status,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'prepared',NOW(),NOW())
            """,
            (
                reference,
                int(admin_user_id),
                snapshot["source_address"],
                asset,
                CANONICAL_USDT_MASTER if asset == "usdt" else None,
                str(amount_raw),
                destination,
                safe_memo,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "reference": reference,
        "status": "prepared",
        "asset": asset,
        "amount_raw": amount_raw,
        "amount_display": format_asset_amount(asset, amount_raw),
        "destination_address": destination,
        "source_address": snapshot["source_address"],
        "memo": safe_memo,
        "expires_in_seconds": WITHDRAW_PREVIEW_TTL_SECONDS,
        "usdt_master": CANONICAL_USDT_MASTER if asset == "usdt" else "",
    }


def _get_prepared_for_confirmation(admin_user_id: int, reference: str) -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")
        cur.execute(
            """
            SELECT id,reference,admin_user_id,source_wallet_address,asset,jetton_master,
                   amount_raw,destination_address,memo,status,created_at
            FROM gram_treasury_withdrawals
            WHERE reference=%s
            FOR UPDATE
            """,
            (str(reference or "").strip(),),
        )
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return {"ok": False, "error": "withdrawal_not_found"}
        if int(row[2]) != int(admin_user_id):
            conn.rollback()
            return {"ok": False, "error": "unauthorized"}
        status = str(row[9] or "")
        if status != "prepared":
            conn.rollback()
            return {"ok": False, "error": "withdrawal_already_processed", "status": status}
        created_at = row[10]
        if isinstance(created_at, datetime):
            created = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created).total_seconds()
            if age > WITHDRAW_PREVIEW_TTL_SECONDS:
                cur.execute(
                    "UPDATE gram_treasury_withdrawals SET status='expired',updated_at=NOW() WHERE id=%s",
                    (int(row[0]),),
                )
                conn.commit()
                return {"ok": False, "error": "withdrawal_expired", "status": "expired"}
        cur.execute(
            """
            UPDATE gram_treasury_withdrawals
            SET status='submitting',confirmed_at=NOW(),updated_at=NOW()
            WHERE id=%s AND status='prepared'
            """,
            (int(row[0]),),
        )
        if int(getattr(cur, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return {"ok": False, "error": "withdrawal_race_blocked"}
        conn.commit()
        return {
            "ok": True,
            "id": int(row[0]),
            "reference": str(row[1]),
            "admin_user_id": int(row[2]),
            "source_wallet_address": str(row[3]),
            "asset": str(row[4]),
            "jetton_master": str(row[5] or ""),
            "amount_raw": int(row[6]),
            "destination_address": str(row[7]),
            "memo": str(row[8] or ""),
            "status": "submitting",
        }
    finally:
        conn.close()


def _update_withdrawal_result(
    withdrawal_id: int,
    status: str,
    tx_hash: str = "",
    query_id: Optional[int] = None,
    error_code: str = "",
) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE gram_treasury_withdrawals
            SET status=%s,tx_hash=%s,query_id=%s,error_code=%s,
                submitted_at=CASE WHEN %s IN ('submitted','submitted_unresolved','submission_uncertain') THEN NOW() ELSE submitted_at END,
                updated_at=NOW()
            WHERE id=%s
            """,
            (
                status,
                str(tx_hash or "") or None,
                str(query_id) if query_id is not None else None,
                str(error_code or "") or None,
                status,
                int(withdrawal_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _load_wallet_for_signing(admin_user_id: int, source: Dict[str, Any]):
    seed_row = _load_signing_seed(admin_user_id, int(source["admin_wallet_id"]), source["source_address"])
    if not seed_row.get("ok"):
        return None, None, None, str(seed_row.get("error") or "signing_seed_unavailable")
    try:
        seed_phrase = decrypt_secret(seed_row["seed_encrypted"])
        wallet, _public_key, private_key, derived_address = _wallet_from_mnemonic(seed_phrase)
    except Exception:
        return None, None, None, "signing_failed"
    if not _same_address(derived_address, source["source_address"]):
        return None, None, None, "wallet_mismatch"
    return wallet, private_key, source["source_address"], ""


def _get_seqno_with_retry(source_address: str) -> Optional[int]:
    try:
        seqno = get_wallet_seqno(source_address)
    except Exception:
        seqno = None
    if seqno is None:
        time.sleep(0.5)
        try:
            seqno = get_wallet_seqno(source_address)
        except Exception:
            seqno = None
    return seqno


def _broadcast_and_resolve(
    boc_base64: str,
    source_address: str,
    outer_destination: str,
    outer_amount_nano: int,
    started_ts: int,
) -> Dict[str, Any]:
    result = send_boc_return_hash(boc_base64)
    if not result.get("ok"):
        # Once submission was attempted, never automatically retry. A transport timeout can
        # be ambiguous even if the API did not return success to us.
        return {
            "ok": False,
            "error": "submission_uncertain",
            "error_detail": str(result.get("error_detail") or "unknown"),
        }
    tx_hash = str(result.get("tx_hash") or "").strip()
    if not tx_hash:
        tx_hash = resolve_recent_ton_tx_hash(
            source_address=source_address,
            destination_address=outer_destination,
            amount_nano=int(outer_amount_nano),
            after_ts=int(started_ts),
            attempts=4,
            delay_seconds=1.5,
        )
    return {
        "ok": True,
        "tx_hash": tx_hash,
        "status": "submitted" if tx_hash else "submitted_unresolved",
    }


def _send_gram(
    admin_user_id: int,
    source: Dict[str, Any],
    destination_address: str,
    amount_raw: int,
    memo: str,
) -> Dict[str, Any]:
    try:
        balance = int(get_ton_balance(source["source_address"]))
    except Exception:
        return {"ok": False, "error": "gram_balance_unavailable"}
    if balance < int(amount_raw) + GRAM_FEE_RESERVE_NANO:
        return {"ok": False, "error": "insufficient_gram_balance"}

    wallet, private_key, source_address, error = _load_wallet_for_signing(admin_user_id, source)
    if error:
        return {"ok": False, "error": error}
    seqno = _get_seqno_with_retry(source_address)
    if seqno is None:
        return {"ok": False, "error": "seqno_unavailable"}
    try:
        transfer = _build_signed_transfer_message(
            wallet=wallet,
            private_key=private_key,
            destination_address=destination_address,
            amount_nano=int(amount_raw),
            seqno=int(seqno),
            comment=memo,
        )
        boc_base64 = _extract_boc_from_transfer(transfer)
    except Exception:
        return {"ok": False, "error": "signing_failed"}
    started_ts = int(time.time())
    return _broadcast_and_resolve(
        boc_base64,
        source_address,
        destination_address,
        int(amount_raw),
        started_ts,
    )


def _build_usdt_transfer_payload(
    destination_address: str,
    response_destination: str,
    amount_raw: int,
    memo: str,
    query_id: int,
):
    if Cell is None or Address is None:
        raise TreasuryWithdrawError("tonsdk_unavailable")
    forward_payload = Cell()
    forward_payload.bits.write_uint(0, 32)  # text comment opcode
    memo_bytes = str(memo or "").encode("utf-8")
    if memo_bytes:
        if hasattr(forward_payload.bits, "write_bytes"):
            forward_payload.bits.write_bytes(memo_bytes)
        elif hasattr(forward_payload.bits, "write_string"):
            forward_payload.bits.write_string(str(memo))
        else:
            raise TreasuryWithdrawError("tonsdk_payload_unsupported")

    body = Cell()
    body.bits.write_uint(0x0F8A7EA5, 32)
    body.bits.write_uint(int(query_id), 64)
    body.bits.write_coins(int(amount_raw))
    body.bits.write_address(Address(destination_address))
    body.bits.write_address(Address(response_destination))
    body.bits.write_bit(0)  # no custom_payload
    body.bits.write_coins(USDT_FORWARD_NANO)
    body.bits.write_bit(1)  # forward_payload is stored by reference
    body.refs.append(forward_payload)
    return body


def _build_jetton_wallet_transfer(wallet, jetton_wallet_address: str, seqno: int, body):
    variants = [
        {"to_addr": jetton_wallet_address, "amount": USDT_TRANSFER_VALUE_NANO, "seqno": int(seqno), "payload": body, "send_mode": 3},
        {"to_addr": jetton_wallet_address, "amount": USDT_TRANSFER_VALUE_NANO, "seqno": int(seqno), "payload": body},
    ]
    for kwargs in variants:
        try:
            return wallet.create_transfer_message(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    raise TreasuryWithdrawError("signing_failed")


def _send_usdt(
    admin_user_id: int,
    source: Dict[str, Any],
    destination_address: str,
    amount_raw: int,
    memo: str,
) -> Dict[str, Any]:
    if not treasury_usdt_withdrawal_enabled():
        return {"ok": False, "error": "usdt_withdraw_disabled"}
    usdt = _fetch_usdt_wallet_state(source["source_address"])
    if not usdt.get("ok"):
        return {"ok": False, "error": str(usdt.get("error") or "usdt_balance_unavailable")}
    if not usdt.get("wallet_address"):
        return {"ok": False, "error": "usdt_wallet_not_deployed"}
    if int(usdt.get("balance_raw") or 0) < int(amount_raw):
        return {"ok": False, "error": "insufficient_usdt_balance"}
    try:
        gram_balance = int(get_ton_balance(source["source_address"]))
    except Exception:
        return {"ok": False, "error": "gram_balance_unavailable"}
    if gram_balance < USDT_TRANSFER_VALUE_NANO + GRAM_FEE_RESERVE_NANO:
        return {"ok": False, "error": "insufficient_gram_for_usdt_gas"}

    wallet, _private_key, source_address, error = _load_wallet_for_signing(admin_user_id, source)
    if error:
        return {"ok": False, "error": error}
    seqno = _get_seqno_with_retry(source_address)
    if seqno is None:
        return {"ok": False, "error": "seqno_unavailable"}
    query_id = secrets.randbits(64)
    try:
        body = _build_usdt_transfer_payload(
            destination_address=destination_address,
            response_destination=source_address,
            amount_raw=int(amount_raw),
            memo=memo,
            query_id=query_id,
        )
        transfer = _build_jetton_wallet_transfer(wallet, usdt["wallet_address"], int(seqno), body)
        boc_base64 = _extract_boc_from_transfer(transfer)
    except Exception:
        return {"ok": False, "error": "signing_failed"}
    started_ts = int(time.time())
    broadcast = _broadcast_and_resolve(
        boc_base64,
        source_address,
        usdt["wallet_address"],
        USDT_TRANSFER_VALUE_NANO,
        started_ts,
    )
    broadcast["query_id"] = query_id
    broadcast["jetton_wallet_address"] = usdt["wallet_address"]
    return broadcast


def confirm_treasury_withdrawal(admin_user_id: int, reference: str) -> Dict[str, Any]:
    _ensure_withdrawal_table()
    if not treasury_withdrawal_enabled():
        return {"ok": False, "error": "treasury_withdraw_disabled"}

    record = _get_prepared_for_confirmation(admin_user_id, reference)
    if not record.get("ok"):
        return record

    source = _resolve_treasury_source(admin_user_id)
    if not source.get("ok") or not _same_address(source.get("source_address", ""), record["source_wallet_address"]):
        error = str(source.get("error") or "treasury_source_changed")
        _update_withdrawal_result(record["id"], "failed", error_code=error)
        return {"ok": False, "error": error, "reference": record["reference"]}

    snapshot = get_treasury_withdraw_snapshot(admin_user_id)
    preflight_error = _validate_preflight(snapshot, record["asset"], int(record["amount_raw"]))
    if preflight_error:
        _update_withdrawal_result(record["id"], "failed", error_code=preflight_error)
        return {"ok": False, "error": preflight_error, "reference": record["reference"]}

    if record["asset"] == "usdt":
        if _normalize_address(record.get("jetton_master", "")) != _normalize_address(CANONICAL_USDT_MASTER):
            _update_withdrawal_result(record["id"], "failed", error_code="usdt_master_mismatch")
            return {"ok": False, "error": "usdt_master_mismatch", "reference": record["reference"]}
        result = _send_usdt(
            admin_user_id,
            source,
            record["destination_address"],
            int(record["amount_raw"]),
            record["memo"],
        )
    else:
        result = _send_gram(
            admin_user_id,
            source,
            record["destination_address"],
            int(record["amount_raw"]),
            record["memo"],
        )

    if result.get("ok"):
        status = str(result.get("status") or ("submitted" if result.get("tx_hash") else "submitted_unresolved"))
        _update_withdrawal_result(
            record["id"],
            status,
            tx_hash=str(result.get("tx_hash") or ""),
            query_id=result.get("query_id"),
        )
        logger.warning(
            "GRAM_TREASURY_WITHDRAW_SUBMITTED reference=%s asset=%s amount_raw=%s tx_hash_present=%s",
            record["reference"], record["asset"], record["amount_raw"], bool(result.get("tx_hash")),
        )
        return {
            "ok": True,
            "reference": record["reference"],
            "status": status,
            "asset": record["asset"],
            "amount_raw": int(record["amount_raw"]),
            "amount_display": format_asset_amount(record["asset"], int(record["amount_raw"])),
            "destination_address": record["destination_address"],
            "tx_hash": str(result.get("tx_hash") or ""),
            "query_id": result.get("query_id"),
        }

    error = str(result.get("error") or "withdraw_failed")
    status = "submission_uncertain" if error == "submission_uncertain" else "failed"
    _update_withdrawal_result(
        record["id"],
        status,
        query_id=result.get("query_id"),
        error_code=error,
    )
    logger.warning(
        "GRAM_TREASURY_WITHDRAW_RESULT reference=%s asset=%s status=%s error=%s",
        record["reference"], record["asset"], status, error,
    )
    return {
        "ok": False,
        "reference": record["reference"],
        "status": status,
        "error": error,
        "error_detail": str(result.get("error_detail") or ""),
    }


def cancel_treasury_withdrawal(admin_user_id: int, reference: str) -> Dict[str, Any]:
    _ensure_withdrawal_table()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE gram_treasury_withdrawals
            SET status='cancelled',updated_at=NOW()
            WHERE reference=%s AND admin_user_id=%s AND status='prepared'
            """,
            (str(reference or "").strip(), int(admin_user_id)),
        )
        changed = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
    finally:
        conn.close()
    return {"ok": bool(changed), "status": "cancelled" if changed else "unchanged"}


def get_recent_treasury_withdrawals(admin_user_id: int, limit: int = 10) -> list[Dict[str, Any]]:
    _ensure_withdrawal_table()
    lim = max(1, min(20, int(limit or 10)))
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT reference,asset,amount_raw,destination_address,memo,status,tx_hash,error_code,created_at,submitted_at
            FROM gram_treasury_withdrawals
            WHERE admin_user_id=%s
            ORDER BY id DESC
            LIMIT %s
            """,
            (int(admin_user_id), lim),
        )
        rows = cur.fetchall() or []
    finally:
        conn.close()
    result = []
    for row in rows:
        amount_raw = int(row[2])
        result.append({
            "reference": str(row[0]),
            "asset": str(row[1]),
            "amount_raw": amount_raw,
            "amount_display": format_asset_amount(str(row[1]), amount_raw),
            "destination_address": str(row[3]),
            "memo": str(row[4] or ""),
            "status": str(row[5]),
            "tx_hash": str(row[6] or ""),
            "error_code": str(row[7] or ""),
            "created_at": row[8],
            "submitted_at": row[9],
        })
    return result
