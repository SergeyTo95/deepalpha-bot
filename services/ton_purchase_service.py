import os
from datetime import datetime

from db.database import get_connection, get_setting, submit_ton_purchase_intent
from services.ton_chain_service import normalize_ton_address, resolve_recent_ton_tx_hash, ton_to_nano


def get_ton_token_price_per_internal_token_nano() -> int:
    explicit_nano = int(str(get_setting("ton_token_price_per_internal_token_nano", "0") or "0"))
    if explicit_nano > 0:
        return explicit_nano
    token_price_ton_raw = str(get_setting("token_price_ton", "0") or "0").strip()
    try:
        token_price_ton = float(token_price_ton_raw)
        if token_price_ton > 0:
            return ton_to_nano(token_price_ton)
    except Exception:
        return 0
    return 0


def _parse_feature_flag_value(raw_value):
    value = str(raw_value or "").strip().lower()
    if value in {"true", "1", "yes", "on", "enabled"}:
        return True
    if value in {"false", "0", "no", "off", "disabled"}:
        return False
    return None


def resolve_ton_purchase_project_wallet() -> str:
    default_purchase_wallet = "UQB7mMWEGE4reqMvHG5zPcHl9fQUy6L91UJhiXgyx772kuUv"
    return (
        os.getenv("TON_PROJECT_WALLET", "")
        or get_setting("ton_project_wallet", "")
        or get_setting("ton_platform_wallet", "")
        or default_purchase_wallet
    ).strip()


def is_ton_wallet_token_purchase_enabled() -> bool:
    env_upper = _parse_feature_flag_value(os.getenv("TON_WALLET_TOKEN_PURCHASE_ENABLED", ""))
    if env_upper is not None:
        return env_upper
    env_lower = _parse_feature_flag_value(os.getenv("ton_wallet_token_purchase_enabled", ""))
    if env_lower is not None:
        return env_lower
    db_value = _parse_feature_flag_value(get_setting("ton_wallet_token_purchase_enabled", "off"))
    return bool(db_value)


def verify_ton_purchase_onchain(intent_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT id, user_id, product_type, wallet_address, project_wallet, expected_amount_nano,
                   status, tx_hash, created_at
            FROM ton_purchase_intents
            WHERE id = %s
            """,
            (intent_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": False, "error": "intent_not_found"}
        (_, _, _, wallet_address, project_wallet, expected_amount_nano, status, tx_hash, created_at) = row
        if str(status or "").strip() == "fulfilled":
            return {"ok": False, "error": "intent_already_fulfilled"}
        try:
            created_ts = int(datetime.fromisoformat(str(created_at)).timestamp())
        except Exception:
            created_ts = None
        expected = int(str(expected_amount_nano or "0"))
        src = normalize_ton_address(str(wallet_address or "").strip())
        dst = normalize_ton_address(str(project_wallet or "").strip())
        if not src or not dst or expected <= 0:
            return {"ok": False, "error": "intent_invalid"}
        h = str(tx_hash or "").strip()
        if not h:
            h = resolve_recent_ton_tx_hash(
                source_address=src,
                destination_address=dst,
                amount_nano=expected,
                after_ts=created_ts,
                attempts=3,
                delay_seconds=1.2,
            )
            if h:
                submit_ton_purchase_intent(int(intent_id), h)
        if not h:
            return {"ok": False, "error": "tx_hash_not_found"}
        cur.execute("SELECT id FROM ton_purchase_intents WHERE tx_hash=%s AND id<>%s", (h, intent_id))
        if cur.fetchone():
            return {"ok": False, "error": "tx_hash_not_unique"}
        cur.execute(
            """
            SELECT wallet_address, destination_address, amount_nano, created_at
            FROM ton_wallet_transactions
            WHERE tx_hash=%s
            ORDER BY id DESC
            LIMIT 1
            """,
            (h,),
        )
        tx = cur.fetchone()
        if tx:
            tx_src = normalize_ton_address(str(tx[0] or "").strip())
            tx_dst = normalize_ton_address(str(tx[1] or "").strip())
            tx_amount = int(str(tx[2] or "0"))
            tx_created = str(tx[3] or "")
            if tx_src != src:
                return {"ok": False, "error": "source_mismatch"}
            if tx_dst != dst:
                return {"ok": False, "error": "destination_mismatch"}
            if tx_amount != expected:
                return {"ok": False, "error": "amount_mismatch"}
            if created_at and tx_created and tx_created < str(created_at):
                return {"ok": False, "error": "timestamp_mismatch"}
        return {"ok": True, "tx_hash": h}
    except Exception as e:
        return {"ok": False, "error": f"verify_failed:{e}"}
    finally:
        conn.close()
