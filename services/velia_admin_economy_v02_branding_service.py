from __future__ import annotations

from typing import Any, Dict

from db.database import get_connection


BRANDING_VERSION = "v0.2-branding-v1"
ASSISTANT_NAME = "Velia"
NEURAL_CORE_NAME = "Velyon Core"
DEEP_NAME = "Velyon Core Deep"


def ensure_economy_v02_branding() -> None:
    """Apply the Velia/Velyon naming boundary once inside draft tables only.

    Velia is the user-facing assistant/chatbot. Velyon Core is the neural
    intelligence behind it. This migration is intentionally isolated from live
    settings, runtime token packages, user balances and subscriptions.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM velia_commercial_draft_versions WHERE version=%s LIMIT 1",
            (BRANDING_VERSION,),
        )
        if cur.fetchone():
            conn.commit()
            return

        plan_notes = {
            "free": (
                "Velia is powered by Velyon Core. Memory and web/search are available with a small fair-use limit. "
                "100 Premium Credits/month plus a one-time 50 Credit welcome bonus."
            ),
            "plus": (
                "Velia with Velyon Core is included. 1,200 Premium Credits/month cover Velyon Core Deep, Images, "
                "Video, Agents and other compute-heavy actions. Priority queue and full personal Memory."
            ),
            "pro": (
                "Velia with higher Velyon Core fair use is included. 3,000 Premium Credits/month cover advanced "
                "Velyon Core Deep, Images, Video, Agents and Developer workloads, plus highest queue priority."
            ),
        }
        core_policies = {
            "free": "Limited · 5 Velyon Core requests/day",
            "plus": "Velyon Core included · generous fair use",
            "pro": "Velyon Core included · high fair use",
        }
        for code, notes in plan_notes.items():
            cur.execute(
                """
                UPDATE velia_commercial_draft_v02_plans
                SET core_policy=%s, notes=%s, updated_at=NOW()
                WHERE code=%s
                """,
                (core_policies[code], notes, code),
            )
            cur.execute(
                """
                UPDATE velia_commercial_draft_plans
                SET notes=%s, updated_at=NOW()
                WHERE code=%s
                """,
                (f"Economy v0.2 · public unit: VELIA Credits · {core_policies[code]}. {notes}", code),
            )

        cur.execute(
            """
            UPDATE velia_commercial_draft_v02_skus
            SET name=%s,
                category='Core',
                notes='Neural intelligence behind Velia. No public Credit debit on paid plans; fair-use, abuse protection and routing stay internal.',
                updated_at=NOW()
            WHERE code='velia_core'
            """,
            (NEURAL_CORE_NAME,),
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_v02_skus
            SET name=%s,
                category='Deep',
                notes='Premium deep-reasoning mode of Velyon Core. Credit charge scales with task size; upstream providers stay invisible.',
                updated_at=NOW()
            WHERE code='velia_deep'
            """,
            (DEEP_NAME,),
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_v02_policies
            SET description='Velyon Core does not consume public Credits on paid plans; Free uses a small fair-use request limit.',
                updated_at=NOW()
            WHERE key='velia_core_credit_debit'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_v02_policies
            SET description='Upstream providers/models are implementation details. Public product terminology is Velia for the assistant and Velyon Core for its neural intelligence.',
                updated_at=NOW()
            WHERE key='upstream_models_public'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=NULL,
                notes='Economy v0.2: Velia is the assistant; Velyon Core is its neural intelligence. Velyon Core is included/fair-use and does not consume public VELIA Credits on paid plans.',
                updated_at=NOW()
            WHERE code='velia_chat'
            """
        )

        cur.execute(
            "INSERT INTO velia_commercial_draft_versions(version,status) VALUES (%s,'draft_only_not_enforced')",
            (BRANDING_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def normalize_public_html(html: str) -> str:
    """Defensive UI normalization for any stale pre-branding draft rows."""
    replacements = (
        ("Public product language is Velia-only.", "Velia is the assistant; Velyon Core is its neural intelligence."),
        ("Velia Core", NEURAL_CORE_NAME),
        ("Velia Deep", DEEP_NAME),
        ("<th>Velia product</th>", "<th>Product</th>"),
        ("Velia-only tiers", "Velia product tiers"),
    )
    result = str(html or "")
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def product_boundary() -> Dict[str, Any]:
    return {
        "assistant": ASSISTANT_NAME,
        "neural_core": NEURAL_CORE_NAME,
        "deep_mode": DEEP_NAME,
        "upstream_models_public": False,
    }
