from pathlib import Path
from types import SimpleNamespace

import pytest

import services.velia_admin_economy_v01_service as economy
import services.velia_admin_economy_v01_ui_patch as economy_ui_patch


def test_v01_plans_and_crypto_discount_are_exact():
    plans = {row[0]: row for row in economy.PLANS}
    assert plans["free"][2:5] == (0.00, 0.00, 100)
    assert plans["plus"][2:5] == (14.99, 10.49, 1200)
    assert plans["pro"][2:5] == (39.99, 27.99, 4000)
    assert economy.CRYPTO_DISCOUNT_PERCENT == 30.0


def test_v01_token_packs_are_exact():
    packs = {row[0]: row for row in economy.TOKEN_PACKS}
    assert packs["pack_250"][2:] == (250, 4.99, 3.49)
    assert packs["pack_800"][2:] == (800, 12.99, 9.09)
    assert packs["pack_2000"][2:] == (2000, 27.99, 19.59)
    assert packs["pack_5000"][2:] == (5000, 59.99, 41.99)
    assert packs["pack_10000"][2:] == (10000, 109.99, 76.99)


def test_plus_and_pro_crypto_worst_case_margin_remains_profitable():
    plus = economy._plan_economics(14.99, 10.49, 1200)
    pro = economy._plan_economics(39.99, 27.99, 4000)
    assert plus["crypto_margin_percent"] == pytest.approx(72.27, abs=0.05)
    assert pro["crypto_margin_percent"] == pytest.approx(65.36, abs=0.05)
    assert pro["crypto_net_per_token_usd"] == pytest.approx(0.0069275, abs=0.000001)
    assert pro["crypto_net_per_token_usd"] > economy.MAX_PROVIDER_COST_PER_VELIA_TOKEN_USD


def test_largest_topup_crypto_margin_stays_above_safety_floor():
    pack = economy._plan_economics(109.99, 76.99, 10000)
    assert pack["crypto_margin_percent"] == pytest.approx(68.51, abs=0.05)
    assert pack["provider_budget_usd"] == pytest.approx(24.0)


def test_kimi_dynamic_token_formulas_and_sample_costs():
    assert economy.core_tokens_for_usage(5000, 1000) == 6
    assert economy.deep_tokens_for_usage(5000, 1000) == 17
    assert economy.kimi_estimated_cost_usd("core", 5000, 1000) == pytest.approx(0.00875)
    assert economy.kimi_estimated_cost_usd("deep", 5000, 1000) == pytest.approx(0.03)
    with pytest.raises(ValueError):
        economy.kimi_estimated_cost_usd("unknown", 1, 1)


def test_every_fixed_image_video_sku_is_profitable_at_cheapest_pro_crypto_token():
    cheapest_net_token = economy._plan_economics(39.99, 27.99, 4000)["crypto_net_per_token_usd"]
    checked = 0
    for sku in economy.SKUS:
        _code, category, _name, _default, min_tokens, _max_tokens, _unit, ceiling, _formula, _notes = sku
        if category not in {"Image", "Video"} or min_tokens is None or ceiling is None:
            continue
        net_value = min_tokens * cheapest_net_token
        margin = economy._margin_percent(net_value, ceiling)
        assert margin is not None
        assert margin >= 50.0, sku[0]
        checked += 1
    assert checked >= 10


def test_v01_policy_guards_match_agreed_commercial_rules():
    policies = {row[0]: row for row in economy.POLICIES}
    assert policies["purchased_tokens_expire"][1] == "false"
    assert policies["spend_subscription_tokens_first"][1] == "true"
    assert policies["discounts_stack"][1] == "false"
    assert policies["subscription_rollover_cap_months"][2] == 1.0
    assert policies["free_welcome_bonus_tokens"][2] == 50.0


def test_v01_is_draft_only_and_cannot_touch_live_billing_or_balances():
    source = Path("services/velia_admin_economy_v01_service.py").read_text(encoding="utf-8")
    assert "draft_only_not_enforced" in source
    assert "UPDATE settings" not in source
    assert "UPDATE token_packages" not in source
    assert "DELETE FROM token_packages" not in source
    assert "UPDATE users" not in source
    assert "token_balance =" not in source
    assert "velia_commercial_draft_versions" in source
    assert "SELECT 1 FROM velia_commercial_draft_versions" in source


def test_v01_ui_attaches_snapshot_before_render_in_threaded_snapshot_path(monkeypatch):
    calls = []
    module = SimpleNamespace()
    module._velia_economy_v01_ui_installed = False

    def base_snapshot():
        calls.append("base_snapshot")
        return {"available": True}

    def base_body(_admin, data):
        calls.append(("base_body", "economy_v01" in data))
        return "BASE"

    module.economy_snapshot = base_snapshot
    module._economy_body = base_body

    monkeypatch.setattr(
        economy_ui_patch,
        "economy_v01_snapshot",
        lambda: calls.append("v01_snapshot") or {"available": True, "version": "v0.1"},
    )
    monkeypatch.setattr(economy_ui_patch, "render_economy_v01", lambda _admin, data: f"V01:{data['version']}|")

    economy_ui_patch.install_economy_v01_ui_patch(module)
    snapshot = module.economy_snapshot()
    html = module._economy_body(object(), snapshot)

    assert calls[:2] == ["base_snapshot", "v01_snapshot"]
    assert calls[2] == ("base_body", True)
    assert html == "V01:v0.1|BASE"
    assert module._velia_economy_v01_ui_installed is True


def test_bootstrap_installs_v01_inside_existing_serialized_economy_boundary():
    source = Path("services/velia_admin_economy_bootstrap_service.py").read_text(encoding="utf-8")
    patch = Path("services/velia_admin_economy_v01_ui_patch.py").read_text(encoding="utf-8")
    assert "ensure_economy_tables()" in source
    assert "ensure_economy_v01_tables()" in source
    assert "install_economy_v01_ui_patch(economy_routes_module)" in source
    assert "pg_advisory_lock" in source
    assert "pg_advisory_unlock" in source
    assert "original_snapshot = economy_routes_module.economy_snapshot" in patch
    assert 'base["economy_v01"] = economy_v01_snapshot()' in patch
