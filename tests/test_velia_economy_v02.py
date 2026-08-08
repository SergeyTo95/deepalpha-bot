from pathlib import Path

import pytest

import services.velia_admin_economy_v02_service as economy
import services.velia_admin_economy_v02_ui_patch as economy_ui_patch


def test_v02_plans_and_crypto_discount_are_exact():
    plans = {row[0]: row for row in economy.PLANS}
    assert plans["free"][2:5] == (0.00, 0.00, 100)
    assert plans["plus"][2:5] == (14.99, 10.49, 1200)
    assert plans["pro"][2:5] == (29.99, 20.99, 3000)
    assert plans["free"][5] == "Limited · 5 Core requests/day"
    assert plans["plus"][5] == "Included · generous fair use"
    assert plans["pro"][5] == "Included · high fair use"
    assert economy.CRYPTO_DISCOUNT_PERCENT == 30.0


def test_v02_credit_packs_are_exact():
    packs = {row[0]: row for row in economy.CREDIT_PACKS}
    assert packs["pack_250"][1:] == ("250 Credits", 250, 4.99, 3.49)
    assert packs["pack_800"][1:] == ("800 Credits", 800, 12.99, 9.09)
    assert packs["pack_2000"][1:] == ("2,000 Credits", 2000, 27.99, 19.59)
    assert packs["pack_5000"][1:] == ("5,000 Credits", 5000, 59.99, 41.99)
    assert packs["pack_10000"][1:] == ("10,000 Credits", 10000, 109.99, 76.99)


def test_included_core_reserve_is_counted_in_paid_plan_worst_case_margin():
    plus = economy._plan_economics(14.99, 10.49, 1200, 0.75)
    pro = economy._plan_economics(29.99, 20.99, 3000, 1.00)
    assert plus["premium_provider_budget_usd"] == pytest.approx(2.88)
    assert plus["provider_budget_usd"] == pytest.approx(3.63)
    assert plus["crypto_margin_percent"] == pytest.approx(65.05, abs=0.05)
    assert pro["premium_provider_budget_usd"] == pytest.approx(7.20)
    assert pro["provider_budget_usd"] == pytest.approx(8.20)
    assert pro["crypto_margin_percent"] == pytest.approx(60.54, abs=0.05)
    assert pro["crypto_net_per_credit_usd"] == pytest.approx(0.0069267, abs=0.000001)
    assert pro["crypto_net_per_credit_usd"] > economy.MAX_PROVIDER_COST_PER_VELIA_CREDIT_USD


def test_largest_topup_crypto_margin_stays_above_safety_floor():
    pack = economy._plan_economics(109.99, 76.99, 10000)
    assert pack["crypto_margin_percent"] == pytest.approx(68.51, abs=0.05)
    assert pack["provider_budget_usd"] == pytest.approx(24.0)


def test_velia_core_is_included_and_deep_is_credit_metered():
    skus = {row[0]: row for row in economy.SKUS}
    core = skus["velia_core"]
    assert core[3:6] == (0, 0, 0)
    assert core[8] == "Included · fair use"
    assert economy.internal_core_cost_units_for_usage(5000, 1000) == 6
    assert economy.deep_credits_for_usage(5000, 1000) == 17
    assert economy.internal_provider_estimated_cost_usd("core", 5000, 1000) == pytest.approx(0.00875)
    assert economy.internal_provider_estimated_cost_usd("deep", 5000, 1000) == pytest.approx(0.03)
    with pytest.raises(ValueError):
        economy.internal_provider_estimated_cost_usd("unknown", 1, 1)


def test_public_commercial_copy_is_velia_only():
    forbidden = ("kimi", "k2.6", "k2.7", "k3", "kling", "veo", "wan", "gemini", "claude", "gpt")
    public_parts = []
    for row in economy.PLANS:
        public_parts.extend((row[1], row[5], row[7]))
    for row in economy.CREDIT_PACKS:
        public_parts.append(row[1])
    for row in economy.SKUS:
        public_parts.extend((row[1], row[2] or "", row[8], row[9]))
    public_text = "\n".join(public_parts).lower()
    for brand in forbidden:
        assert brand not in public_text
    assert "velia core" in public_text
    assert "velia images" in public_text
    assert "velia video" in public_text


def test_ui_snapshot_guarantees_velia_deep_display_name():
    normalized = economy_ui_patch._normalize_v02_snapshot({
        "available": True,
        "skus": [{"code": "velia_deep", "name": ""}],
    })
    assert normalized["skus"][0]["name"] == "Velia Deep"


def test_legacy_visible_labels_are_normalized_to_credits():
    html = (
        "What is a VELIA token? VELIA Token Token balances Current token packages "
        "Draft feature token prices Included VELIA tokens / month Tokens/action Token Ledger "
        "Future commercial model Draft plans"
    )
    normalized = economy_ui_patch._normalize_visible_credit_labels(html)
    assert "What are VELIA Credits?" in normalized
    assert "VELIA Token" not in normalized
    assert "Credit balances" in normalized
    assert "Current runtime Credit packages" in normalized
    assert "Included VELIA Credits / month" in normalized
    assert "Credits/action" in normalized
    assert "Credit Ledger" in normalized
    assert "Legacy Stage 2 draft workspace" in normalized


def test_every_fixed_image_video_sku_is_profitable_at_cheapest_pro_crypto_credit():
    cheapest_net_credit = economy._plan_economics(29.99, 20.99, 3000, 1.00)["crypto_net_per_credit_usd"]
    checked = 0
    for sku in economy.SKUS:
        _code, category, _name, _default, min_credits, _max_credits, _unit, ceiling, _formula, _notes = sku
        if category not in {"Images", "Video"} or min_credits is None or ceiling is None:
            continue
        net_value = min_credits * cheapest_net_credit
        margin = economy._margin_percent(net_value, ceiling)
        assert margin is not None
        assert margin >= 50.0, sku[0]
        checked += 1
    assert checked >= 10


def test_v02_policy_guards_match_agreed_commercial_rules():
    policies = {row[0]: row for row in economy.POLICIES}
    assert policies["public_usage_unit"][1] == "VELIA Credits"
    assert policies["velia_core_credit_debit"][1] == "included_fair_use"
    assert policies["upstream_models_public"][1] == "false"
    assert policies["purchased_credits_expire"][1] == "false"
    assert policies["spend_subscription_credits_first"][1] == "true"
    assert policies["discounts_stack"][1] == "false"
    assert policies["subscription_rollover_cap_months"][2] == 1.0
    assert policies["free_welcome_bonus_credits"][2] == 50.0


def test_v02_is_draft_only_and_cannot_touch_live_billing_or_balances():
    source = Path("services/velia_admin_economy_v02_service.py").read_text(encoding="utf-8")
    assert "draft_only_not_enforced" in source
    assert "velia_commercial_draft_versions" in source
    assert "SELECT 1 FROM velia_commercial_draft_versions" in source
    assert "UPDATE settings" not in source
    assert "UPDATE token_packages" not in source
    assert "DELETE FROM token_packages" not in source
    assert "UPDATE users" not in source
    assert "token_balance =" not in source


def test_bootstrap_installs_v02_inside_existing_serialized_economy_boundary():
    source = Path("services/velia_admin_economy_bootstrap_service.py").read_text(encoding="utf-8")
    assert "ensure_economy_tables()" in source
    assert "ensure_economy_v02_tables()" in source
    assert "install_economy_v02_ui_patch(economy_routes_module)" in source
    assert "pg_advisory_lock" in source
    assert "pg_advisory_unlock" in source


def test_v02_ui_patch_keeps_db_snapshot_inside_existing_worker_thread():
    source = Path("services/velia_admin_economy_v02_ui_patch.py").read_text(encoding="utf-8")
    assert "original_snapshot = economy_routes_module.economy_snapshot" in source
    assert 'base["economy_v02"] = _normalize_v02_snapshot(economy_v02_snapshot())' in source
    assert "original_body = economy_routes_module._economy_body" in source
    assert "economy_v02_snapshot()" not in source.split("def wrapped_body", 1)[1]
