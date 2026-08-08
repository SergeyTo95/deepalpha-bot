from __future__ import annotations

import math
from typing import Any, Dict, List

from db.database import get_connection


ECONOMY_VERSION = "v0.1"
STORE_FEE_ASSUMPTION_PERCENT = 15.0
CRYPTO_DISCOUNT_PERCENT = 30.0
CRYPTO_PAYMENT_RESERVE_PERCENT = 1.0
MAX_PROVIDER_COST_PER_VELIA_TOKEN_USD = 0.0024
FREE_WELCOME_BONUS_TOKENS = 50

PLANS = (
    (
        "free",
        "Free",
        0.00,
        0.00,
        100,
        "100 monthly tokens; VELIA Core; Standard Images; premium video disabled; +50 one-time welcome bonus.",
    ),
    (
        "plus",
        "Plus",
        14.99,
        10.49,
        1200,
        "Core plus metered Deep; Pro Images; Standard/Pro Video; priority queue and normal Memory context.",
    ),
    (
        "pro",
        "Pro",
        39.99,
        27.99,
        4000,
        "Full metered Deep/K3 access, K2.7 Code, premium Images, Veo/Kling/Cinema Video, highest queue priority.",
    ),
)

TOKEN_PACKS = (
    ("pack_250", "250 tokens", 250, 4.99, 3.49),
    ("pack_800", "800 tokens", 800, 12.99, 9.09),
    ("pack_2000", "2,000 tokens", 2000, 27.99, 19.59),
    ("pack_5000", "5,000 tokens", 5000, 59.99, 41.99),
    ("pack_10000", "10,000 tokens", 10000, 109.99, 76.99),
)

# code, category, name, default_tokens, min_tokens, max_tokens, unit, cost ceiling, formula, notes
SKUS = (
    (
        "velia_core",
        "AI",
        "VELIA Core",
        None,
        None,
        None,
        "request",
        None,
        "ceil(1 + 0.5 × input_k + 2 × output_k)",
        "Designed around Kimi K2.6/K2.7-class economics. Token charge scales with actual usage.",
    ),
    (
        "velia_deep",
        "AI",
        "VELIA Deep",
        None,
        None,
        None,
        "request",
        None,
        "ceil(2 + 1.5 × input_k + 7 × output_k)",
        "Designed around Kimi K3-class economics. No unlimited K3 promise; usage remains token-metered.",
    ),
    ("image_standard", "Image", "Image Standard", 10, 10, 10, "image", 0.03, "10 tokens", "Standard generation route."),
    ("image_pro", "Image", "Image Pro", 20, 20, 20, "image", 0.06, "20 tokens", "Higher-quality provider route."),
    ("image_ultra", "Image", "Image Ultra / 4K", 50, 50, 50, "image", 0.10, "50 tokens", "Premium/4K route; $0.10 provider-cost safety ceiling."),
    ("image_edit", "Image", "Premium Edit / Remix", 40, 30, 50, "image", 0.10, "30–50 tokens; default 40", "Complexity/provider dependent draft band."),
    ("video_standard_5s", "Video", "Video Standard · 5 sec", 110, 110, 110, "video", 0.25, "110 tokens", "Budget route such as Wan-class generation."),
    ("video_standard_10s", "Video", "Video Standard · 10 sec", 220, 220, 220, "video", 0.50, "220 tokens", "Budget route such as Wan-class generation."),
    ("video_pro_5s", "Video", "Video Pro · 5 sec", 190, 190, 190, "video", 0.42, "190 tokens", "Kling O3-class no-audio cost ceiling."),
    ("video_pro_audio_5s", "Video", "Video Pro + audio · 5 sec", 250, 250, 250, "video", 0.56, "250 tokens", "Kling O3-class with audio cost ceiling."),
    ("video_pro_plus_audio_5s", "Video", "Video Pro+ native audio · 5 sec", 380, 380, 380, "video", 0.84, "380 tokens", "Premium native-audio cost ceiling."),
    ("video_veo_fast_8s", "Video", "Veo Fast · 8 sec", 360, 360, 360, "video", 0.80, "360 tokens", "Veo Fast-class cost ceiling."),
    ("video_veo_31_8s", "Video", "Veo 3.1 · 8 sec", 720, 720, 720, "video", 1.60, "720 tokens", "Veo 3.1-class cost ceiling."),
    ("video_cinema_4k_5s", "Video", "Cinema 4K · 5 sec", 950, 950, 950, "video", 2.10, "950 tokens", "Native 4K premium-video cost ceiling."),
    ("video_cinema_4k_10s", "Video", "Cinema 4K · 10 sec", 1900, 1900, 1900, "video", 4.20, "1900 tokens", "Native 4K premium-video cost ceiling."),
)

# code, provider, model, metric, rate, unit, notes
PROVIDER_COSTS = (
    ("kimi_core_cache", "Kimi", "K2.6", "cache_hit", 0.16, "USD / 1M tokens", "Economy v0.1 planning assumption."),
    ("kimi_core_input", "Kimi", "K2.6", "input", 0.95, "USD / 1M tokens", "Economy v0.1 planning assumption."),
    ("kimi_core_output", "Kimi", "K2.6", "output", 4.00, "USD / 1M tokens", "Economy v0.1 planning assumption."),
    ("kimi_deep_cache", "Kimi", "K3", "cache_hit", 0.30, "USD / 1M tokens", "Economy v0.1 planning assumption."),
    ("kimi_deep_input", "Kimi", "K3", "input", 3.00, "USD / 1M tokens", "Economy v0.1 planning assumption."),
    ("kimi_deep_output", "Kimi", "K3", "output", 15.00, "USD / 1M tokens", "Economy v0.1 planning assumption."),
)

POLICIES = (
    ("crypto_discount_percent", "30", 30.0, "USDT checkout target discount versus Store retail price."),
    ("store_fee_assumption_percent", "15", 15.0, "Planning assumption for eligible app-store commission; actual store rules remain authoritative."),
    ("crypto_payment_reserve_percent", "1", 1.0, "Operational reserve for crypto payment/RPC/reconciliation overhead."),
    ("max_provider_cost_per_velia_token_usd", "0.0024", 0.0024, "Hard planning ceiling used for worst-case subscription and pack margins."),
    ("free_welcome_bonus_tokens", "50", 50.0, "One-time acquisition bonus in addition to Free monthly allowance."),
    ("subscription_rollover_cap_months", "1", 1.0, "Subscription tokens may roll over by at most one additional monthly allowance."),
    ("purchased_tokens_expire", "false", 0.0, "Purchased top-up tokens do not expire in Economy v0.1."),
    ("spend_subscription_tokens_first", "true", 1.0, "Spend subscription allowance before purchased token balance."),
    ("discounts_stack", "false", 0.0, "Crypto -30% does not stack with another generic discount; use the single best eligible offer."),
    ("commercial_status", "draft_only_not_enforced", None, "Economy v0.1 is planning data only until a separate activation change is approved."),
)


def core_tokens_for_usage(input_tokens: int, output_tokens: int) -> int:
    return max(1, int(math.ceil(1 + 0.5 * (max(0, input_tokens) / 1000.0) + 2 * (max(0, output_tokens) / 1000.0))))


def deep_tokens_for_usage(input_tokens: int, output_tokens: int) -> int:
    return max(2, int(math.ceil(2 + 1.5 * (max(0, input_tokens) / 1000.0) + 7 * (max(0, output_tokens) / 1000.0))))


def kimi_estimated_cost_usd(mode: str, input_tokens: int, output_tokens: int) -> float:
    normalized = str(mode or "").strip().lower()
    if normalized == "core":
        input_rate, output_rate = 0.95, 4.00
    elif normalized == "deep":
        input_rate, output_rate = 3.00, 15.00
    else:
        raise ValueError("unsupported_kimi_mode")
    return (max(0, input_tokens) / 1_000_000.0) * input_rate + (max(0, output_tokens) / 1_000_000.0) * output_rate


def _margin_percent(net_revenue: float, provider_budget: float) -> float | None:
    if net_revenue <= 0:
        return None
    return ((net_revenue - provider_budget) / net_revenue) * 100.0


def _plan_economics(store_price: float, crypto_price: float, tokens: int) -> Dict[str, Any]:
    store_net = store_price * (1 - STORE_FEE_ASSUMPTION_PERCENT / 100.0)
    crypto_net = crypto_price * (1 - CRYPTO_PAYMENT_RESERVE_PERCENT / 100.0)
    provider_budget = tokens * MAX_PROVIDER_COST_PER_VELIA_TOKEN_USD
    return {
        "store_net_usd": store_net,
        "crypto_net_usd": crypto_net,
        "provider_budget_usd": provider_budget,
        "store_margin_percent": _margin_percent(store_net, provider_budget),
        "crypto_margin_percent": _margin_percent(crypto_net, provider_budget),
        "store_net_per_token_usd": (store_net / tokens) if tokens else None,
        "crypto_net_per_token_usd": (crypto_net / tokens) if tokens else None,
    }


def ensure_economy_v01_tables() -> None:
    """Install and seed Economy v0.1 exactly once without touching live billing.

    The version marker prevents future deployments from overwriting manual draft
    edits. Only VELIA draft tables are updated; runtime settings, runtime token
    packages, user balances and subscriptions are deliberately untouched.
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
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v01_plans (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                store_price_usd NUMERIC(12,2) NOT NULL,
                crypto_price_usd NUMERIC(12,2) NOT NULL,
                monthly_tokens INTEGER NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v01_token_packs (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                store_price_usd NUMERIC(12,2) NOT NULL,
                crypto_price_usd NUMERIC(12,2) NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v01_skus (
                code TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                default_tokens INTEGER,
                min_tokens INTEGER,
                max_tokens INTEGER,
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
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v01_provider_costs (
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
            CREATE TABLE IF NOT EXISTS velia_commercial_draft_v01_policies (
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

        for code, name, store, crypto, tokens, notes in PLANS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v01_plans(code,name,store_price_usd,crypto_price_usd,monthly_tokens,notes)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, store, crypto, tokens, notes),
            )

        for code, name, tokens, store, crypto in TOKEN_PACKS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v01_token_packs(code,name,tokens,store_price_usd,crypto_price_usd,notes)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                (code, name, tokens, store, crypto, "Top-up tokens are more expensive per token than Pro subscription tokens."),
            )

        for sku in SKUS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v01_skus(
                    code,category,name,default_tokens,min_tokens,max_tokens,unit_label,
                    provider_cost_ceiling_usd,pricing_formula,notes
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                sku,
            )

        for item in PROVIDER_COSTS:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v01_provider_costs(code,provider,model,metric,rate_usd,unit_label,notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
                """,
                item,
            )

        for item in POLICIES:
            cur.execute(
                """
                INSERT INTO velia_commercial_draft_v01_policies(key,value_text,value_numeric,description)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
                """,
                item,
            )

        # Mirror the basic v0.1 values into the original Stage 2 draft workspace
        # so the existing editable cards immediately show the agreed plan.
        for code, _name, store, _crypto, tokens, notes in PLANS:
            cur.execute(
                """
                UPDATE velia_commercial_draft_plans
                SET monthly_price_usd=%s, monthly_tokens=%s, notes=%s, updated_at=NOW()
                WHERE code=%s
                """,
                (store, tokens, notes, code),
            )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=NULL,
                notes='VELIA Core dynamic formula: ceil(1 + 0.5 × input_k + 2 × output_k). Deep uses a separate K3-class formula.',
                updated_at=NOW()
            WHERE code='velia_chat'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=10,
                notes='Economy v0.1: Standard 10; Pro 20; Ultra/4K 50; premium Edit/Remix 30–50 (default 40).',
                updated_at=NOW()
            WHERE code='image_generation'
            """
        )
        cur.execute(
            """
            UPDATE velia_commercial_draft_features
            SET tokens_per_action=110,
                notes='Economy v0.1 starts at Standard 5 sec = 110. Detailed video SKUs are shown in the v0.1 matrix above.',
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


def economy_v01_snapshot() -> Dict[str, Any]:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT to_regclass('public.velia_commercial_draft_v01_plans')")
        if not _row(cur.fetchone(), 0):
            return {"available": False, "reason": "v01_tables_not_bootstrapped"}

        cur.execute("SELECT code,name,store_price_usd,crypto_price_usd,monthly_tokens,notes FROM velia_commercial_draft_v01_plans ORDER BY CASE code WHEN 'free' THEN 1 WHEN 'plus' THEN 2 WHEN 'pro' THEN 3 ELSE 99 END")
        plans: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            item = {
                "code": str(_row(row, 0, "")),
                "name": str(_row(row, 1, "")),
                "store_price_usd": float(_row(row, 2, 0) or 0),
                "crypto_price_usd": float(_row(row, 3, 0) or 0),
                "monthly_tokens": int(_row(row, 4, 0) or 0),
                "notes": str(_row(row, 5, "") or ""),
            }
            item.update(_plan_economics(item["store_price_usd"], item["crypto_price_usd"], item["monthly_tokens"]))
            plans.append(item)

        cur.execute("SELECT code,name,tokens,store_price_usd,crypto_price_usd,notes FROM velia_commercial_draft_v01_token_packs ORDER BY tokens")
        packs: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            item = {
                "code": str(_row(row, 0, "")),
                "name": str(_row(row, 1, "")),
                "tokens": int(_row(row, 2, 0) or 0),
                "store_price_usd": float(_row(row, 3, 0) or 0),
                "crypto_price_usd": float(_row(row, 4, 0) or 0),
                "notes": str(_row(row, 5, "") or ""),
            }
            item.update(_plan_economics(item["store_price_usd"], item["crypto_price_usd"], item["tokens"]))
            packs.append(item)

        paid_crypto_unit_values = [p.get("crypto_net_per_token_usd") for p in plans + packs if p.get("crypto_net_per_token_usd")]
        cheapest_net_per_token = min(paid_crypto_unit_values) if paid_crypto_unit_values else None

        cur.execute("SELECT code,category,name,default_tokens,min_tokens,max_tokens,unit_label,provider_cost_ceiling_usd,pricing_formula,notes FROM velia_commercial_draft_v01_skus ORDER BY CASE category WHEN 'AI' THEN 1 WHEN 'Image' THEN 2 WHEN 'Video' THEN 3 ELSE 99 END, code")
        skus: List[Dict[str, Any]] = []
        for row in cur.fetchall() or []:
            min_tokens = None if _row(row, 4) is None else int(_row(row, 4))
            ceiling = None if _row(row, 7) is None else float(_row(row, 7))
            conservative_net = (min_tokens * cheapest_net_per_token) if min_tokens and cheapest_net_per_token else None
            item = {
                "code": str(_row(row, 0, "")),
                "category": str(_row(row, 1, "")),
                "name": str(_row(row, 2, "")),
                "default_tokens": None if _row(row, 3) is None else int(_row(row, 3)),
                "min_tokens": min_tokens,
                "max_tokens": None if _row(row, 5) is None else int(_row(row, 5)),
                "unit_label": str(_row(row, 6, "")),
                "provider_cost_ceiling_usd": ceiling,
                "pricing_formula": str(_row(row, 8, "")),
                "notes": str(_row(row, 9, "") or ""),
                "conservative_net_value_usd": conservative_net,
                "conservative_margin_percent": _margin_percent(conservative_net or 0.0, ceiling or 0.0) if ceiling is not None and conservative_net else None,
            }
            skus.append(item)

        cur.execute("SELECT provider,model,metric,rate_usd,unit_label,notes FROM velia_commercial_draft_v01_provider_costs ORDER BY provider,model,metric")
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

        cur.execute("SELECT key,value_text,value_numeric,description FROM velia_commercial_draft_v01_policies ORDER BY key")
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
            "token_packs": packs,
            "skus": skus,
            "provider_costs": provider_costs,
            "policies": policies,
            "cheapest_crypto_net_per_token_usd": cheapest_net_per_token,
            "provider_cost_ceiling_per_token_usd": MAX_PROVIDER_COST_PER_VELIA_TOKEN_USD,
            "core_sample": {
                "input_tokens": 5000,
                "output_tokens": 1000,
                "velia_tokens": core_tokens_for_usage(5000, 1000),
                "provider_cost_usd": kimi_estimated_cost_usd("core", 5000, 1000),
            },
            "deep_sample": {
                "input_tokens": 5000,
                "output_tokens": 1000,
                "velia_tokens": deep_tokens_for_usage(5000, 1000),
                "provider_cost_usd": kimi_estimated_cost_usd("deep", 5000, 1000),
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


def render_economy_v01(admin: Any, data: Dict[str, Any]) -> str:
    if not data.get("available"):
        return (
            "<div class='card full' style='border-color:rgba(246,200,95,.36)'>"
            "<div class='label'>Economy v0.1</div><h2>Draft model unavailable</h2>"
            f"<div class='muted'>{admin._e(data.get('reason') or 'unknown')}</div></div>"
        )

    plan_rows = "".join(
        "<tr>"
        f"<td><b>{admin._e(p['name'])}</b><div class='hint'>{admin._e(p['notes'])}</div></td>"
        f"<td>{_usd(p['store_price_usd'])}</td><td>{_usd(p['crypto_price_usd'])}</td>"
        f"<td>{admin._metric(p['monthly_tokens'])}</td>"
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
        for p in data.get("token_packs") or []
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
    cheapest = data.get("cheapest_crypto_net_per_token_usd")
    ceiling = data.get("provider_cost_ceiling_per_token_usd")
    headroom = (float(cheapest) - float(ceiling)) if cheapest is not None and ceiling is not None else None

    return f"""
<div class='card full' style='border-color:rgba(76,209,139,.42);background:linear-gradient(145deg,rgba(18,56,42,.35),rgba(9,13,20,.97));margin-bottom:12px'>
  <div class='label'>VELIA Economy {admin._e(data.get('version') or '')}</div>
  <div class='value' style='font-size:22px'>DRAFT ONLY · NOT ENFORCED</div>
  <div class='hint'>The agreed commercial model is persisted separately from live billing. Deployment of this draft cannot change a user charge, subscription or token debit.</div>
</div>
<div class='grid' style='margin-bottom:12px'>
  <div class='card'><div class='label'>Crypto discount</div><div class='value'>−30%</div><div class='hint'>USDT versus Store retail; generic discounts do not stack.</div></div>
  <div class='card'><div class='label'>Cheapest paid token · net</div><div class='value'>{_usd(cheapest, 6)}</div><div class='hint'>Conservative Pro+Crypto acquisition value after 1% reserve.</div></div>
  <div class='card'><div class='label'>Provider budget ceiling</div><div class='value'>{_usd(ceiling, 4)}</div><div class='hint'>Per consumed VELIA Token.</div></div>
  <div class='card'><div class='label'>Headroom / token</div><div class='value'>{_usd(headroom, 6)}</div><div class='hint'>Before infra, support, taxes and profit.</div></div>
</div>
<div class='card full'><h2>Plans · Store vs USDT</h2><div class='table-wrap'><table><thead><tr><th>Plan</th><th>Store</th><th>USDT</th><th>Tokens/mo</th><th>Worst-case provider budget</th><th>Store margin</th><th>Crypto margin</th></tr></thead><tbody>{plan_rows}</tbody></table></div><div class='hint'>Margin uses 15% Store fee assumption, 1% crypto reserve and a $0.0024 provider-cost ceiling per consumed VELIA Token.</div></div>
<div class='card full' style='margin-top:12px'><h2>Token top-ups</h2><div class='table-wrap'><table><thead><tr><th>Pack</th><th>Store</th><th>USDT</th><th>Worst-case provider budget</th><th>Store margin</th><th>Crypto margin</th></tr></thead><tbody>{pack_rows}</tbody></table></div><div class='hint'>Top-ups intentionally cost more per token than Pro subscription. Purchased tokens do not expire; subscription allowance is spent first.</div></div>
<div class='card full' style='margin-top:12px'><h2>AI / Image / Video token matrix</h2><div class='table-wrap'><table><thead><tr><th>Type</th><th>Product</th><th>Token price</th><th>Provider ceiling</th><th>Margin at cheapest token</th></tr></thead><tbody>{sku_rows}</tbody></table></div><div class='hint'>Margin uses the minimum token charge for ranged SKUs and the cheapest Pro+Crypto net token value. Dynamic AI rows intentionally have no fixed provider ceiling per request.</div></div>
<div class='grid' style='margin-top:12px'>
  <div class='card wide'><div class='label'>Kimi Core sample</div><h2>5k input + 1k output</h2><div class='value'>{admin._metric(core.get('velia_tokens'))} VELIA tokens</div><div class='hint'>Planning provider cost {_usd(core.get('provider_cost_usd'), 5)} using K2.6 input/output rates.</div></div>
  <div class='card wide'><div class='label'>Kimi Deep sample</div><h2>5k input + 1k output</h2><div class='value'>{admin._metric(deep.get('velia_tokens'))} VELIA tokens</div><div class='hint'>Planning provider cost {_usd(deep.get('provider_cost_usd'), 5)} using K3 input/output rates.</div></div>
</div>
<div class='card full' style='margin-top:12px'><h2>Provider cost assumptions</h2><div class='table-wrap'><table><thead><tr><th>Provider</th><th>Model</th><th>Metric</th><th>Rate</th><th>Note</th></tr></thead><tbody>{provider_rows}</tbody></table></div></div>
<div class='card full' style='margin-top:12px'><h2>Commercial rules</h2><div class='table-wrap'><table><thead><tr><th>Rule</th><th>Value</th><th>Description</th></tr></thead><tbody>{policy_rows}</tbody></table></div></div>
"""


def install_economy_v01_ui_patch(economy_routes_module: Any) -> None:
    if getattr(economy_routes_module, "_velia_economy_v01_ui_installed", False):
        return
    original_body = economy_routes_module._economy_body

    def wrapped_body(admin: Any, data: Dict[str, Any]) -> str:
        v01 = economy_v01_snapshot()
        return render_economy_v01(admin, v01) + original_body(admin, data)

    economy_routes_module._economy_body = wrapped_body
    economy_routes_module._velia_economy_v01_ui_installed = True
