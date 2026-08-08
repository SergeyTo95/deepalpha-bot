from __future__ import annotations

from db.database import get_connection


TOPUPS_VERSION = "v0.2-topups-v1"
MINI_PACK_CODE = "pack_100"
MINI_PACK_NAME = "100 Credits"
MINI_PACK_CREDITS = 100
MINI_PACK_STORE_USD = 2.49
MINI_PACK_CRYPTO_USD = 1.74


def ensure_economy_v02_topups() -> None:
    """Add the agreed 100-Credit entry pack once inside draft tables only.

    This migration is intentionally isolated from live settings, runtime token
    packages, user balances, subscriptions and payment acceptance. The version
    marker prevents later deploys from overwriting manual draft edits.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM velia_commercial_draft_versions WHERE version=%s LIMIT 1",
            (TOPUPS_VERSION,),
        )
        if cur.fetchone():
            conn.commit()
            return

        cur.execute(
            """
            INSERT INTO velia_commercial_draft_v02_credit_packs(
                code,name,credits,store_price_usd,crypto_price_usd,notes
            ) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (code) DO NOTHING
            """,
            (
                MINI_PACK_CODE,
                MINI_PACK_NAME,
                MINI_PACK_CREDITS,
                MINI_PACK_STORE_USD,
                MINI_PACK_CRYPTO_USD,
                "Entry top-up for a small premium-compute need. Purchased Credits do not expire; subscription Credits are spent first.",
            ),
        )
        cur.execute(
            "INSERT INTO velia_commercial_draft_versions(version,status) VALUES (%s,'draft_only_not_enforced')",
            (TOPUPS_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def mini_pack_economics() -> dict[str, float]:
    store_net = MINI_PACK_STORE_USD * 0.85
    crypto_net = MINI_PACK_CRYPTO_USD * 0.99
    provider_budget = MINI_PACK_CREDITS * 0.0024
    return {
        "store_net_usd": store_net,
        "crypto_net_usd": crypto_net,
        "provider_budget_usd": provider_budget,
        "store_margin_percent": ((store_net - provider_budget) / store_net) * 100.0,
        "crypto_margin_percent": ((crypto_net - provider_budget) / crypto_net) * 100.0,
    }
