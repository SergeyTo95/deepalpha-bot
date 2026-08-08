from __future__ import annotations

import math
from typing import Any, Dict, List

from db.database import get_connection


ECONOMY_VERSION = "v0.2"
STORE_FEE_ASSUMPTION_PERCENT = 15.0
CRYPTO_DISCOUNT_PERCENT = 30.0
CRYPTO_PAYMENT_RESERVE_PERCENT = 1.0
MAX_PROVIDER_COST_PER_VELIA_CREDIT_USD = 0.0024
FREE_WELCOME_BONUS_CREDITS = 50

# code, name, Store price, USDT price, included premium Credits, Core policy,
# monthly Core provider reserve, public-facing plan notes.
PLANS = (
    (
        "free",
        "Free",
        0.00,
        0.00,
        100,
        "Limited · 5 Core requests/day",
        0.25,
        "Velia Core, Memory and web/search are available with a small fair-use limit. 100 Premium Credits/month plus a one-time 50 Credit welcome bonus.",
    ),
    (
        "plus",
        "Plus",
        14.99,
        10.49,
        1200,
        "Included · generous fair use",
        0.75,
        "Velia Core is included. 1,200 Premium Credits/month cover Deep, Images, Video, Agents and other compute-heavy actions. Priority queue and full personal Memory.",
    ),
    (
        "pro",
        "Pro",
        29.99,
        20.99,
        3000,
        "Included · high fair use",
        1.00,
        "Velia Core is included with higher fair use. 3,000 Premium Credits/month for advanced Deep, Images, Video, Agents and Developer workloads, plus highest queue priority.",
    ),
)

CREDIT_PACKS = (
    ("pack_250", "250 Credits", 250, 4.99, 3.49),
    ("pack_800", "800 Credits", 800, 12.99, 9.09),
    ("pack_2000", "2,000 Credits", 2000, 27.99, 19.59),
    ("pack_5000", "5,000 Credits", 5000, 59.99, 41.99),
    ("pack_10000", "10,000 Credits", 10000, 109.99, 76.99),
)

# Public/commercial SKU definitions never mention upstream model/provider brands.
# code, category, name, default Credits, min Credits, max Credits, unit,
# provider-cost ceiling, public pricing text, public/admin-safe notes.
SKUS = (
    (
        "velia_core",
        "Core",
        "Velia Core",
        0,
        0,
        0,
        "conversation",
        None,
        "Included · fair use",
        "No public Credit debit. Rate/abuse protection and provider routing stay internal to Velia.",
    ),
    (
        "velia_deep",
        "Deep",
        None,
        None,
        None,
        None,
        "request",
        None,
        "ceil(2 + 1.5 × input_k + 7 × output_k) Credits",
        "Premium reasoning compute. Credit charge scales with task size; the user only sees Velia Deep.",
    ),
    ("image_standard", "Images", "Velia Images Standard", 10, 10, 10, "image", 0.03, "10 Credits", "Standard image generation."),
    ("image_pro", "Images", "Velia Images Pro", 20, 20, 20, "image", 0.06, "20 Credits", "Higher-quality image generation."),
    ("image_ultra", "Images", "Velia Images Ultra / 4K", 50, 50, 50, "image", 0.10, "50 Credits", "Premium / high-resolution image generation."),
    ("image_edit", "Images", "Velia Images Edit / Remix", 40, 30, 50, "image", 0.10, "30–50 Credits · default 40", "Complexity-dependent premium image edit/remix."),
    ("video_standard_5s", "Video", "Velia Video Standard · 5 sec", 110, 110, 110, "video", 0.25, "110 Credits", "Standard video generation."),
    ("video_standard_10s", "Video", "Velia Video Standard · 10 sec", 220, 220, 220, "video", 0.50, "220 Credits", "Standard longer video generation."),
    ("video_pro_5s", "Video", "Velia Video Pro · 5 sec", 190, 190, 190, "video", 0.42, "190 Credits", "Higher-quality video generation."),
    ("video_pro_audio_5s", "Video", "Velia Video Pro + Audio · 5 sec", 250, 250, 250, "video", 0.56, "250 Credits", "Higher-quality video generation with audio."),
    ("video_pro_plus_audio_5s", "Video", "Velia Video Pro+ · 5 sec + Audio", 380, 380, 380, "video", 0.84, "380 Credits", "Premium video generation with native audio."),
    ("video_ultra_8s", "Video", "Velia Video Ultra · 8 sec", 360, 360, 360, "video", 0.80, "360 Credits", "Premium fast 8-second generation tier."),
    ("video_ultra_plus_8s", "Video", "Velia Video Ultra+ · 8 sec", 720, 720, 720, "video", 1.60, "720 Credits", "Higher-compute premium 8-second generation tier."),
    ("video_cinema_4k_5s", "Video", "Velia Video Cinema 4K · 5 sec", 950, 950, 950, "video", 2.10, "950 Credits", "Cinema-grade 4K generation tier."),
    ("video_cinema_4k_10s", "Video", "Velia Video Cinema 4K · 10 sec", 1900, 1900, 1900, "video", 4.20, "1,900 Credits", "Cinema-grade 4K longer generation tier."),
    ("agent_task", "Agents", "Velia Agents", None, None, None, "task", None, "Metered by task complexity", "Premium autonomous work; final pricing will be attached to measured compute before activation."),
    ("developer_task", "Developer", "Velia Developer", None, None, None, "task", None, "Metered by task complexity", "Coding/developer work; final pricing will be attached to measured compute before activation."),
)

# Internal admin-only provider cost telemetry. These names are deliberately not
# copied into public plan/SKU labels or product-facing commercial copy.
PROVIDER_COSTS = (
    ("kimi_core_cache", "Kimi", "K2.6", "cache_hit", 0.16, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
    ("kimi_core_input", "Kimi", "K2.6", "input", 0.95, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
    ("kimi_core_output", "Kimi", "K2.6", "output", 4.00, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
    ("kimi_deep_cache", "Kimi", "K3", "cache_hit", 0.30, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
    ("kimi_deep_input", "Kimi", "K3", "input", 3.00, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
    ("kimi_deep_output", "Kimi", "K3", "output", 15.00, "USD / 1M tokens", "Internal cost planning only; never product-facing."),
)

POLICIES = (
    ("public_usage_unit", "VELIA Credits", None, "Public paid-compute unit. Internal database field names may remain token-based for compatibility."),
    ("velia_core_credit_debit", "included_fair_use", 0.0, "Velia Core does not consume public Credits on paid plans; Free uses a small fair-use request limit."),
    ("free_core_requests_per_day", "5", 5.0, "Draft Free acquisition limit; not enforced by this PR."),
    ("crypto_discount_percent", "30", 30.0, "USDT checkout target discount versus Store retail price."),
    ("store_fee_assumption_percent", "15", 15.0, "Planning assumption for eligible app-store commission; actual store rules remain authoritative."),
    ("crypto_payment_reserve_percent", "1", 1.0, "Operational reserve for crypto payment/RPC/reconciliation overhead."),
    ("max_provider_cost_per_velia_credit_usd", "0.0024", 0.0024, "Hard planning ceiling used for premium Credit worst-case margins."),
    ("free_welcome_bonus_credits", "50", 50.0, "One-time acquisition bonus in addition to Free monthly Premium Credits."),
    ("subscription_rollover_cap_months", "1", 1.0, "Subscription Credits may roll over by at most one additional monthly allowance."),
    ("purchased_credits_expire", "false", 0.0, "Purchased top-up Credits do not expire in Economy v0.2."),
    ("spend_subscription_credits_first", "true", 1.0, "Spend subscription allowance before purchased Credit balance."),
    ("discounts_stack", "false", 0.0, "Crypto -30% does not stack with another generic discount; use the single best eligible offer."),
    ("upstream_models_public", "false", 0.0, "Upstream providers/models are implementation details. The user sees Velia only."),
    ("commercial_status", "draft_only_not_enforced", None, "Economy v0.2 is planning data only until a separate activation change is approved."),
)


def deep_credits_for_usage(input_tokens: int, output_tokens: int) -> int:
    return max(2, int(math.ceil(2 + 1.5 * (max(0, input_tokens) / 1000.0) + 7 * (max(0, output_tokens) / 1000.0))))


def internal_core_cost_units_for_usage(input_tokens: int, output_tokens: int) -> int:
    """Internal cost-equivalent units only; Velia Core does not debit public Credits."""
    return max(1, int(math.ceil(1 + 0.5 * (max(0, input_tokens) / 1000.0) + 2 * (max(0, output_tokens) / 1000.0))))


def internal_provider_estimated_cost_usd(mode: str, input_tokens: int, output_tokens: int) -> float:
    normalized = str(mode or "").strip().lower()
    if normalized == "core":
        input_rate, output_rate = 0.95, 4.00
    elif normalized == "deep":
        input_rate, output_rate = 3.00, 15.00
    else:
        raise ValueError("unsupported_internal_cost_mode")
    return (max(0, input_tokens) / 1_000_000.0) * input_rate + (max(0, output_tokens) / 1_000_000.0) * output_rate


def _margin_percent(net_revenue: float, provider_budget: float) -> float | None:
    if net_revenue <= 0:
        return None
    return ((net_revenue - provider_budget) / net_revenue) * 100.0


def _plan_economics(store_price: float, crypto_price: float, credits: int, core_cost_reserve_usd: float = 0.0) -> Dict[str, Any]:
    store_net = store_price * (1 - STORE_FEE_ASSUMPTION_PERCENT / 100.0)
    crypto_net = crypto_price * (1 - CRYPTO_PAYMENT_RESERVE_PERCENT / 100.0)
    premium_provider_budget = credits * MAX_PROVIDER_COST_PER_VELIA_CREDIT_USD
    total_provider_budget = premium_provider_budget + max(0.0, float(core_cost_reserve_usd or 0.0))
    return {
        "store_net_usd": store_net,
        "crypto_net_usd": crypto_net,
        "premium_provider_budget_usd": premium_provider_budget,
        "core_cost_reserve_usd": max(0.0, float(core_cost_reserve_usd or 0.0)),
        "provider_budget_usd": total_provider_budget,
        "store_margin_percent": _margin_percent(store_net, total_provider_budget),
        "crypto_margin_percent": _margin_percent(crypto_net, total_provider_budget),
        "store_net_per_credit_usd": (store_net / credits) if credits else None,
        "crypto_net_per_credit_usd": (crypto_net / credits) if credits else None,
    }


def ensure_economy_v02_tables() -> None:
    """Install and seed Economy v0.2 once without touching live billing.

    v0.2 exists only in draft tables. Runtime settings, runtime token packages,
    user balances and subscriptions are deliberately untouched. A version marker
    prevents later deploys from overwriting manual draft edits.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_versions (
                version TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'draft',
                applied_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v02_plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                store_price_usd NUMERIC(12,2) NOT NULL,
                crypto_price_usd NUMERIC(12,2) NOT NULL,
                monthly_credits INTEGER NOT NULL,
                core_policy TEXT NOT NULL,
                core_cost_reserve_usd NUMERIC(12,6) NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v02_credit_packs (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                credits INTEGER NOT NULL,
                store_price_usd NUMERIC(12,2) NOT NULL,
                crypto_price_usd NUMERIC(12,2) NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v02_skus (
                code TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                name TEXT,
                default_credits INTEGER,
                min_credits INTEGER,
                max_credits INTEGER,
                unit_label TEXT NOT NULL,
                provider_cost_ceiling_usd NUMERIC(18,6),
                pricing_formula TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v02_provider_costs (
                code TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                metric TEXT NOT NULL,
                rate_usd NUMERIC(18,6) NOT NULL,
                unit_label TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v02_policies (
                key TEXT PRIMARY KEY,
                value_text TEXT,
                value_numeric NUMERIC(18,6),
                description TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )

        cur.execute("SELECT 1 FROM velia_commercial_draft_versions WHERE version=%s LIMIT 1", (ECONOMY_VERSION,))
        if cur.fetchone():
            conn.commit()
            return

        for code, name, store, crypto, credits, core_policy, core_reserve, notes in PLANS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v02_plans(
                    code,name,store_price_usd,crypto_price_usd,monthly_credits,
                    core_policy,core_cost_reserve_usd,notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, store, crypto, credits, core_policy, core_reserve, notes),
            )

        for code, name, credits, store, crypto in CREDIT_PACKS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v02_credit_packs(code,name,credits,store_price_usd,crypto_price_usd,notes)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, credits, store, crypto, "Top-up Credits intentionally cost more per Credit than the Pro subscription allowance."),
            )

        for sku in SKUS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v02_skus(
                    code,category,name,default_credits,min_credits,max_credits,unit_label,
                    provider_cost_ceiling_usd,pricing_formula,notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                sku,
            )

        for item in PROVIDER_COSTS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v02_provider_costs(code,provider,model,metric,rate_usd,unit_label,notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                item,
            )

        for item in POLICIES:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v02_policies(key,value_text,value_numeric,description)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
                """,
                item,
            )

        # Mirror only public planning values into the original Stage 2 draft
        # cards. These are still draft tables; live settings/billing are untouched.
        for code, _name, store, _crypto, credits, core_policy, _reserve, notes in PLANS:
            cur.execute(
                """
                UPDATE velia_commercial_draft_plans
                SET monthly_price_usd=%s, monthly_tokens=%s, notes=%s, updated_at=NOW()
                WHERE code=%s
                """,
                (store, credits, f"Economy v0.2 · public unit: VELIA Credits · Core: {core_policy}. {notes}", code),
            )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=NULL,
                notes='Economy v0.2: Velia Core is included/fair-use and does not consume public VELIA Credits. Upstream models/providers are not product-facing.',
                updated_at=NOW()
            WHERE code='velia_chat'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=10,
                notes='Economy v0.2: Velia Images Standard 10 Credits; Pro 20; Ultra/4K 50; Edit/Remix 30–50 (default 40).',
                updated_at=NOW()
            WHERE code='image_generation'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=110,
                notes='Economy v0.2: Velia Video starts at Standard 5 sec = 110 Credits. Detailed Velia-only tiers are shown above.',
                updated_at=NOW()
            WHERE code='video_generation'
            """
        )

        cur.execute(
            "INSERT INTO velia_commercial_draft_versions(version,status) VALUES (%s,'draft_only_not_enforced')",
            (ECONOMY_VERSION,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _row(row: Any, index: int, default: Any = None) -> Any:
    try:
        return row[index]
    except (IndexError, TypeError):
        return default


def economy_v02_snapshot() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT to_regclass('public.velia_commercial_draft_v02_plans')")
        if not _row(cur.fetchone(), 0):
            return {"available": False, "reason": "v02_tables_not_bootstrapped"}

        cur.execute(
            "SELECT code,name,store_price_usd,crypto_price_usd,monthly_credits,core_policy,core_cost_reserve_usd,notes "
            "FROM velia_commercial_draft_v02_plans "
            "ORDER BY CASE code WHEN 'free' THEN 1 WHEN 'plus' THEN 2 WHEN 'pro' THEN 3 ELSE 99 END"
        )
        plans: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            item = {
                "code": str(_row(row, 0, "")),
                "name": str(_row(row, 1, "")),
                "store_price_usd": float(_row(row, 2, 0) or 0),
                "crypto_price_usd": float(_row(row, 3, 0) or 0),
                "monthly_credits": int(_row(row, 4, 0) or 0),
                "core_policy": str(_row(row, 5, "") or ""),
                "core_cost_reserve_usd": float(_row(row, 6, 0) or 0),
                "notes": str(_row(row, 7, "") or ""),
            }
            item.update(
                _plan_economics(
                    item["store_price_usd"],
                    item["crypto_price_usd"],
                    item["monthly_credits"],
                    item["core_cost_reserve_usd"],
                )
            )
            plans.append(item)

        cur.execute(
            "SELECT code,name,credits,store_price_usd,crypto_price_usd,notes "
            "FROM velia_commercial_draft_v02_credit_packs ORDER BY credits"
        )
        packs: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            item = {
                "code": str(_row(row, 0, "")),
                "name": str(_row(row, 1, "")),
                "credits": int(_row(row, 2, 0) or 0),
                "store_price_usd": float(_row(row, 3, 0) or 0),
                "crypto_price_usd": float(_row(row, 4, 0) or 0),
                "notes": str(_row(row, 5, "") or ""),
            }
            item.update(_plan_economics(item["store_price_usd"], item["crypto_price_usd"], item["credits"]))
            packs.append(item)

        paid_crypto_unit_values = [
            p.get("crypto_net_per_credit_usd")
            for p in plans + packs
            if p.get("crypto_net_per_credit_usd")
        ]
        cheapest_net_per_credit = min(paid_crypto_unit_values) if paid_crypto_unit_values else None

        cur.execute(
            "SELECT code,category,name,default_credits,min_credits,max_credits,unit_label,provider_cost_ceiling_usd,pricing_formula,notes "
            "FROM velia_commercial_draft_v02_skus "
            "ORDER BY CASE category WHEN 'Core' THEN 1 WHEN 'Deep' THEN 2 WHEN 'Images' THEN 3 WHEN 'Video' THEN 4 WHEN 'Agents' THEN 5 WHEN 'Developer' THEN 6 ELSE 99 END, code"
        )
        skus: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            min_credits = None if _row(row, 4) is None else int(_row(row, 4))
            ceiling = None if _row(row, 7) is None else float(_row(row, 7))
            conservative_net = (
                min_credits * cheapest_net_per_credit
                if min_credits and cheapest_net_per_credit
                else None
            )
            skus.append({
                "code": str(_row(row, 0, "")),
                "category": str(_row(row, 1, "")),
                "name": str(_row(row, 2, "") or ""),
                "default_credits": None if _row(row, 3) is None else int(_row(row, 3)),
                "min_credits": min_credits,
                "max_credits": None if _row(row, 5) is None else int(_row(row, 5)),
                "unit_label": str(_row(row, 6, "")),
                "provider_cost_ceiling_usd": ceiling,
                "pricing_formula": str(_row(row, 8, "")),
                "notes": str(_row(row, 9, "") or ""),
                "conservative_net_value_usd": conservative_net,
                "conservative_margin_percent": (
                    _margin_percent(conservative_net, ceiling)
                    if conservative_net is not None and ceiling is not None
                    else None
                ),
            })

        cur.execute(
            "SELECT provider,model,metric,rate_usd,unit_label,notes "
            "FROM velia_commercial_draft_v02_provider_costs ORDER BY provider,model,metric"
        )
        provider_costs = [
            {
                "provider": str(_row(row, 0, "")),
                "model": str(_row(row, 1, "")),
                "metric": str(_row(row, 2, "")),
                "rate_usd": float(_row(row, 3, 0) or 0),
                "unit_label": str(_row(row, 4, "")),
                "notes": str(_row(row, 5, "") or ""),
            }
            for row in (cur.fetchall() or [])
        ]

        cur.execute(
            "SELECT key,value_text,value_numeric,description "
            "FROM velia_commercial_draft_v02_policies ORDER BY key"
        )
        policies = [
            {
                "key": str(_row(row, 0, "")),
                "value_text": None if _row(row, 1) is None else str(_row(row, 1)),
                "value_numeric": None if _row(row, 2) is None else float(_row(row, 2)),
                "description": str(_row(row, 3, "") or ""),
            }
            for row in (cur.fetchall() or [])
        ]

        return {
            "available": True,
            "version": ECONOMY_VERSION,
            "status": "draft_only_not_enforced",
            "plans": plans,
            "credit_packs": packs,
            "skus": skus,
            "provider_costs": provider_costs,
            "policies": policies,
            "cheapest_crypto_net_per_credit_usd": cheapest_net_per_credit,
            "provider_cost_ceiling_per_credit_usd": MAX_PROVIDER_COST_PER_VELIA_CREDIT_USD,
            "core_sample": {
                "input_tokens": 5000,
                "output_tokens": 1000,
                "public_credits": 0,
                "internal_cost_units": internal_core_cost_units_for_usage(5000, 1000),
                "provider_cost_usd": internal_provider_estimated_cost_usd("core", 5000, 1000),
            },
            "deep_sample": {
                "input_tokens": 5000,
                "output_tokens": 1000,
                "velia_credits": deep_credits_for_usage(5000, 1000),
                "provider_cost_usd": internal_provider_estimated_cost_usd("deep", 5000, 1000),
            },
        }
    except Exception as exc:
        return {"available": False, "reason": exc.__class__.__name__}
    finally:
        cur.close()
        conn.close()


def _usd(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"${float(value):.{digits}f}"


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def render_economy_v02(admin: Any, data: Dict[str, Any]) -> str:
    if not data.get("available"):
        return (
            "<div class='card full' style='border-color:rgba(246,200,95,.36)'>"
            "<div class='label'>Economy v0.2</div><h2>Draft model unavailable</h2>"
            f"<div class='muted'>{admin._e(data.get('reason') or 'unknown')}</div></div>"
        )

    plan_rows = "".join(
        "<tr>"
        f"<td><b>{admin._e(p['name'])}</b><div class='hint'>{admin._e(p['notes'])}</div></td>"
        f"<td>{_usd(p['store_price_usd'])}</td><td>{_usd(p['crypto_price_usd'])}</td>"
        f"<td>{admin._metric(p['monthly_credits'])}</td>"
        f"<td>{admin._e(p['core_policy'])}</td>"
        f"<td>{_usd(p['provider_budget_usd'])}</td>"
        f"<td>{_pct(p['store_margin_percent'])}</td><td>{_pct(p['crypto_margin_percent'])}</td>"
        "</tr>"
        for p in data.get("plans") or []
    )

    pack_rows = "".join(
        "<tr>"
        f"<td>{admin._e(p['name'])}</td><td>{_usd(p['store_price_usd'])}</td><td>{_usd(p['crypto_price_usd'])}</td>"
        f"<td>{_usd(p['provider_budget_usd'])}</td><td>{_pct(p['store_margin_percent'])}</td><td>{_pct(p['crypto_margin_percent'])}</td>"
        "</tr>"
        for p in data.get("credit_packs") or []
    )

    sku_rows = "".join(
        "<tr>"
        f"<td>{admin._e(s['category'])}</td><td><b>{admin._e(s['name'])}</b><div class='hint'>{admin._e(s['notes'])}</div></td>"
        f"<td><code>{admin._e(s['pricing_formula'])}</code></td>"
        f"<td>{_usd(s['provider_cost_ceiling_usd'])}</td>"
        f"<td>{_pct(s['conservative_margin_percent'])}</td>"
        "</tr>"
        for s in data.get("skus") or []
    )

    provider_rows = "".join(
        "<tr>"
        f"<td>{admin._e(p['provider'])}</td><td>{admin._e(p['model'])}</td><td>{admin._e(p['metric'])}</td>"
        f"<td>{_usd(p['rate_usd'], 4)} / 1M</td><td class='hint'>{admin._e(p['notes'])}</td>"
        "</tr>"
        for p in data.get("provider_costs") or []
    )

    policy_rows = "".join(
        "<tr>"
        f"<td><code>{admin._e(p['key'])}</code></td><td>{admin._e(p['value_text'] or '')}</td><td>{admin._e(p['description'])}</td>"
        "</tr>"
        for p in data.get("policies") or []
    )

    core = data.get("core_sample") or {}
    deep = data.get("deep_sample") or {}
    cheapest = data.get("cheapest_crypto_net_per_credit_usd")
    ceiling = data.get("provider_cost_ceiling_per_credit_usd")
    headroom = (float(cheapest) - float(ceiling)) if cheapest is not None and ceiling is not None else None

    return f"""
<div class='card full' style='border-color:rgba(76,209,139,.42);background:linear-gradient(145deg,rgba(18,56,42,.35),rgba(9,13,20,.97));margin-bottom:12px'>
  <div class='label'>VELIA Economy {admin._e(data.get('version') or '')}</div>
  <div class='value' style='font-size:22px'>DRAFT ONLY · NOT ENFORCED</div>
  <div class='hint'>Public product language is Velia-only. Upstream models/providers remain internal implementation and cost telemetry. This draft cannot change a user charge, subscription or Credit debit.</div>
</div>
<div class='grid' style='margin-bottom:12px'>
  <div class='card'><div class='label'>Crypto discount</div><div class='value'>−30%</div><div class='hint'>USDT versus Store retail; generic discounts do not stack.</div></div>
  <div class='card'><div class='label'>Cheapest paid Credit · net</div><div class='value'>{_usd(cheapest, 6)}</div><div class='hint'>Conservative Pro+Crypto acquisition value after 1% reserve.</div></div>
  <div class='card'><div class='label'>Provider budget ceiling</div><div class='value'>{_usd(ceiling, 4)}</div><div class='hint'>Per consumed Premium Credit.</div></div>
  <div class='card'><div class='label'>Headroom / Credit</div><div class='value'>{_usd(headroom, 6)}</div><div class='hint'>Before infra, support, taxes and profit.</div></div>
</div>
<div class='card full'><h2>Plans · Store vs USDT</h2><div class='table-wrap'><table><thead><tr><th>Plan</th><th>Store</th><th>USDT</th><th>Credits/mo</th><th>Velia Core</th><th>Worst-case provider budget</th><th>Store margin</th><th>Crypto margin</th></tr></thead><tbody>{plan_rows}</tbody></table></div><div class='hint'>Paid-plan margin includes the Premium Credit ceiling plus a monthly reserve for included Velia Core fair-use: Plus $0.75, Pro $1.00. Free uses a $0.25 acquisition-cost reserve.</div></div>
<div class='card full' style='margin-top:12px'><h2>Credit top-ups</h2><div class='table-wrap'><table><thead><tr><th>Pack</th><th>Store</th><th>USDT</th><th>Worst-case provider budget</th><th>Store margin</th><th>Crypto margin</th></tr></thead><tbody>{pack_rows}</tbody></table></div><div class='hint'>Top-ups intentionally cost more per Credit than Pro subscription. Purchased Credits do not expire; subscription Credits are spent first.</div></div>
<div class='card full' style='margin-top:12px'><h2>Velia premium-compute matrix</h2><div class='table-wrap'><table><thead><tr><th>Type</th><th>Velia product</th><th>Credit price</th><th>Provider ceiling</th><th>Margin at cheapest Credit</th></tr></thead><tbody>{sku_rows}</tbody></table></div><div class='hint'>Velia Core is included/fair-use. Deep, Images, Video, Agents and Developer use Premium Credits where compute is materially expensive.</div></div>
<div class='grid' style='margin-top:12px'>
  <div class='card wide'><div class='label'>Velia Core · internal cost sample</div><h2>5k input + 1k output</h2><div class='value'>Included · 0 public Credits</div><div class='hint'>Internal provider estimate {_usd(core.get('provider_cost_usd'), 5)}. Routing details are not product-facing.</div></div>
  <div class='card wide'><div class='label'>Velia Deep · unit-economics sample</div><h2>5k input + 1k output</h2><div class='value'>{admin._metric(deep.get('velia_credits'))} Credits</div><div class='hint'>Internal provider estimate {_usd(deep.get('provider_cost_usd'), 5)}. The user sees Velia Deep only.</div></div>
</div>
<div class='card full' style='margin-top:12px;border-color:rgba(246,200,95,.28)'><h2>Internal provider cost telemetry · admin only</h2><div class='hint' style='margin-bottom:8px'>Never expose these provider/model names in plans, checkout, app copy or feature names. They exist only so we can route Velia profitably.</div><div class='table-wrap'><table><thead><tr><th>Provider</th><th>Model</th><th>Metric</th><th>Rate</th><th>Note</th></tr></thead><tbody>{provider_rows}</tbody></table></div></div>
<div class='card full' style='margin-top:12px'><h2>Commercial rules</h2><div class='table-wrap'><table><thead><tr><th>Rule</th><th>Value</th><th>Description</th></tr></thead><tbody>{policy_rows}</tbody></table></div></div>
"""
