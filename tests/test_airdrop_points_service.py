import importlib
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))


def reload_points(monkeypatch):
    import services.airdrop_points_service as svc
    svc = importlib.reload(svc)
    monkeypatch.setattr(svc, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    svc._MEMORY_LEDGER.clear()
    svc._MEMORY_ID = 1
    svc._MEMORY_REFERRAL_ACTIVATIONS.clear()
    return svc


def test_new_user_balance_and_formatter(monkeypatch):
    svc = reload_points(monkeypatch)
    assert svc.get_airdrop_points_balance(1)["points"] == 0
    ru = svc.format_airdrop_status(1, "ru")
    en = svc.format_airdrop_status(1, "en")
    assert "Твои баллы: 0" in ru
    assert "Монета: Soon" in ru
    assert "Your Points: 0" in en
    assert "Coin: Soon" in en


def test_award_points_default_amount(monkeypatch):
    svc = reload_points(monkeypatch)
    result = svc.award_airdrop_points(1, "analysis_completed")
    assert result["awarded"] is True
    assert result["amount"] == 10
    assert svc.get_airdrop_points_balance(1)["points"] == 10


def test_daily_cap(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setenv("AIRDROP_DAILY_CAP", "20")
    assert svc.award_airdrop_points(1, "analysis_completed")["amount"] == 10
    assert svc.award_airdrop_points(1, "analysis_completed")["amount"] == 10
    assert svc.award_airdrop_points(1, "analysis_completed")["awarded"] is False
    assert svc.get_airdrop_points_balance(1)["points"] == 20


def test_disabled_points(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setenv("AIRDROP_POINTS_ENABLED", "false")
    result = svc.award_airdrop_points(1, "analysis_completed")
    assert result["awarded"] is False
    assert svc.get_airdrop_points_balance(1)["points"] == 0


def test_denied_live_does_not_award(monkeypatch):
    svc = reload_points(monkeypatch)
    from services import live_analyst_service as live_svc
    monkeypatch.setattr(live_svc, "can_user_access_live", lambda user_id: {"allowed": False, "mode": "owner_only"})
    result = live_svc.process_live_text(77, "BTC?", ui_language="en")
    assert result["access_denied"] is True
    assert svc.get_airdrop_points_balance(77)["points"] == 0


def test_failed_analysis_does_not_award(monkeypatch):
    svc = reload_points(monkeypatch)
    assert svc.get_airdrop_points_balance(55)["points"] == 0
    # Unsupported/failed paths should not call award; an unsupported reason is rejected and balance stays unchanged.
    result = svc.award_airdrop_points(55, "failed_analysis")
    assert result["awarded"] is False
    assert svc.get_airdrop_points_balance(55)["points"] == 0


def test_wording_safety(monkeypatch):
    points = reload_points(monkeypatch)
    from services import live_access_control_service as access
    texts = [
        points.format_airdrop_status(1, "ru"),
        points.format_airdrop_status(1, "en"),
        access.format_live_access_denied_message("ru"),
        access.format_live_access_denied_message("en"),
    ]
    assert "Монета: Soon" in texts[0]
    assert "Coin: Soon" in texts[1]
    assert "Монета: Soon" in texts[2]
    assert "Coin: Soon" in texts[3]
    assert "Топ-анализ" in texts[0]
    assert "Top Analysis" in texts[1]
    for text in texts:
        assert "DeepAlpha Points" in text
        assert "Soon" in text
        low = text.lower()
        for forbidden in ["listing", "price", "profit", "guaranteed", "ticker", "allocation", "future rewards", "may be considered"]:
            assert forbidden not in low


def test_successful_analysis_reason_awards(monkeypatch):
    svc = reload_points(monkeypatch)
    svc.award_airdrop_points(1, "analysis_completed")
    svc.award_airdrop_points(1, "live_analysis_completed")
    assert svc.get_airdrop_points_balance(1)["points"] == 20
    history = svc.get_airdrop_points_history(1)
    assert {row["reason"] for row in history} == {"analysis_completed", "live_analysis_completed"}


def test_award_analysis_points_top_source(monkeypatch):
    svc = reload_points(monkeypatch)
    result = svc.award_analysis_points(1, source="top_analysis")
    assert result["awarded"] is True
    assert svc.get_airdrop_points_balance(1)["points"] == 10
    assert svc.get_airdrop_points_history(1)[0]["metadata"]["source"] == "top_analysis"


def test_daily_cap_shared_by_top_and_normal(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setenv("AIRDROP_DAILY_CAP", "20")
    assert svc.award_analysis_points(1, "top_analysis")["amount"] == 10
    assert svc.award_analysis_points(1, "telegram_quick_analysis")["amount"] == 10
    assert svc.award_analysis_points(1, "webapp_analysis")["awarded"] is False
    assert svc.get_airdrop_points_balance(1)["points"] == 20


def test_referral_no_referrer(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setattr(svc, "get_user", lambda user_id: {"user_id": user_id, "referred_by": None})
    result = svc.award_referral_activation_points(2, {"source": "top_analysis"})
    assert result["awarded"] is False
    assert result["reason"] == "no_referrer"


def test_referral_activation(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setattr(svc, "get_user", lambda user_id: {"user_id": user_id, "referred_by": 1})
    result = svc.award_referral_activation_points(2, {"source": "telegram_quick_analysis"})
    assert result["awarded"] is True
    assert svc.get_airdrop_points_balance(1)["points"] == 50
    assert svc.get_airdrop_points_balance(2)["points"] == 20
    reasons = {row["reason"] for uid in (1, 2) for row in svc.get_airdrop_points_history(uid)}
    assert reasons == {"referral_first_analysis_referrer", "referral_first_analysis_referred"}


def test_referral_idempotency(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setattr(svc, "get_user", lambda user_id: {"user_id": user_id, "referred_by": 1})
    assert svc.award_referral_activation_points(2, {"source": "telegram_quick_analysis"})["awarded"] is True
    assert svc.award_referral_activation_points(2, {"source": "top_analysis"})["awarded"] is False
    assert svc.get_airdrop_points_balance(1)["points"] == 50
    assert svc.get_airdrop_points_balance(2)["points"] == 20


def test_self_referral(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setattr(svc, "get_user", lambda user_id: {"user_id": user_id, "referred_by": user_id})
    assert svc.award_referral_activation_points(2)["reason"] == "self_referral"
    assert svc.get_airdrop_points_balance(2)["points"] == 0


def test_disabled_referral_points(monkeypatch):
    svc = reload_points(monkeypatch)
    monkeypatch.setenv("AIRDROP_REFERRAL_POINTS_ENABLED", "false")
    monkeypatch.setattr(svc, "get_user", lambda user_id: {"user_id": user_id, "referred_by": 1})
    result = svc.award_referral_activation_points(2)
    assert result["awarded"] is False
    assert svc.get_airdrop_points_balance(1)["points"] == 0
