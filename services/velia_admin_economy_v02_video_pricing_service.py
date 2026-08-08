from __future__ import annotations

from db.database import get_connection


VIDEO_PRICING_VERSION = "v0.2-video-standard-100-v1"
STANDARD_VIDEO_CODE = "video_standard_5s"
STANDARD_VIDEO_CREDITS = 100
STANDARD_VIDEO_PROVIDER_CEILING_USD = 0.25


def ensure_economy_v02_video_pricing() -> None:
    """Apply the agreed 100-Credit Standard 5s video price once, draft-only.

    This migration intentionally updates only the Economy draft tables. It does
    not touch live settings, runtime token packages, user balances,
    subscriptions, payment acceptance or fulfillment.

    Existing manual draft overrides are preserved: the SKU/compatibility row is
    changed only when it still contains the previous 110-Credit draft value.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM velia_commercial_draft_versions WHERE version=%s LIMIT 1",
            (VIDEO_PRICING_VERSION,),
        )
        if cur.fetchone():
            conn.commit()
            return

        cur.execute(
            """
            UPDATE velia_commercial_draft_v02_skus
            SET default_credits=%s,
                min_credits=%s,
                max_credits=%s,
                pricing_formula=%s,
                notes=%s,
                updated_at=NOW()
            WHERE code=%s
              AND default_credits=110
              AND min_credits=110
              AND max_credits=110
            """,
            (
                STANDARD_VIDEO_CREDITS,
                STANDARD_VIDEO_CREDITS,
                STANDARD_VIDEO_CREDITS,
                f"{STANDARD_VIDEO_CREDITS} Credits",
                "Standard 5-second video generation. 100 Credits intentionally aligns the smallest top-up with one standard video.",
                STANDARD_VIDEO_CODE,
            ),
        )

        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=%s,
                notes=%s,
                updated_at=NOW()
            WHERE code='video_generation'
              AND tokens_per_action=110
            """,
            (
                STANDARD_VIDEO_CREDITS,
                "Economy v0.2: Velia Video starts at Standard 5 sec = 100 Credits. Detailed tiers remain in the versioned Economy draft above.",
            ),
        )

        cur.execute(
            "INSERT INTO velia_commercial_draft_versions(version,status) VALUES (%s,'draft_only_not_enforced')",
            (VIDEO_PRICING_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def standard_video_margin_at_pro_crypto() -> float:
    """Worst-case provider-budget margin at the cheapest paid Credit source."""
    crypto_net_per_credit = (20.99 * 0.99) / 3000.0
    net_value = STANDARD_VIDEO_CREDITS * crypto_net_per_credit
    return ((net_value - STANDARD_VIDEO_PROVIDER_CEILING_USD) / net_value) * 100.0
