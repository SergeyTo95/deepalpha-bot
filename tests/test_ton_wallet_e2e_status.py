import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if "requests" not in sys.modules:
    fake_requests = types.SimpleNamespace(get=lambda *a, **k: None, post=lambda *a, **k: None)
    sys.modules["requests"] = fake_requests


def test_ton_wallet_runtime_status_disabled(monkeypatch):
    monkeypatch.setenv("TON_WALLET_ENABLED", "false")
    monkeypatch.setenv("TON_NETWORK", "mainnet")
    from services.ton_wallet_service import get_ton_wallet_runtime_status
    status = get_ton_wallet_runtime_status()
    assert status["enabled"] is False
    assert status["effective_enabled"] is False
    assert status["network"] == "mainnet"
    assert status["reason"] == "disabled"


def test_ton_wallet_balance_reports_stale_on_toncenter_failure(monkeypatch):
    from services import ton_wallet_service as svc
    monkeypatch.setattr(svc, "get_or_create_user_ton_wallet", lambda user_id: {"ok": True, "wallet_address": "UQabc", "network": "mainnet", "last_balance_nano": "123", "last_balance_checked_at": "old", "id": 1})
    monkeypatch.setattr(svc, "get_ton_balance", lambda address: (_ for _ in ()).throw(RuntimeError("toncenter_down")))
    res = svc.get_user_ton_balance(1, refresh=True)
    assert res["ok"] is True
    assert res["balance_nano"] == "123"
    assert res["balance_stale"] is True
    assert "toncenter_down" in res["refresh_error"]


def test_admin_menu_has_gram_wallets():
    py = open("bot/admin.py", encoding="utf-8").read()
    assert "💎 Gram Wallets" in py
    assert "admin_gram_wallets" in py
    assert "admin_gram_wallets_text" in py


def test_webapp_has_disabled_wallet_visibility_copy():
    js = open("webapp/app.js", encoding="utf-8").read()
    assert "tonEffectiveEnabled" in js
    assert "tonWalletUnavailable" in js
    assert "tonSetupError" in js
    assert "tonNetworkError" in js


def test_web_api_exposes_distinct_wallet_fields():
    py = open("web.py", encoding="utf-8").read()
    assert "user_custodial_wallet_address" in py
    assert "cashier_purchase_wallet" in py
    assert "ton_purchase_wallet" in py  # compatibility only


def test_telegram_screen_defines_address_for_all_languages():
    py = open("telegram_bot.py", encoding="utf-8").read()
    marker = "async def _send_ton_wallet_screen"
    body = py[py.index(marker):py.index("def _short_ton_value", py.index(marker))]
    assert 'address_html = html.escape' in body
    assert 'if lang == "ru":\n        text =' in body
    assert 'else:\n        text =' in body
