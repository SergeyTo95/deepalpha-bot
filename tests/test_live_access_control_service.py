import importlib
import sys
import types

sys.modules.setdefault("requests", types.SimpleNamespace(post=lambda *args, **kwargs: None))
sys.modules.setdefault("psycopg2", types.SimpleNamespace(connect=lambda *a, **k: None, extras=types.SimpleNamespace(RealDictCursor=object), errors=types.SimpleNamespace()))
sys.modules.setdefault("psycopg2.extras", types.SimpleNamespace(RealDictCursor=object))


def reload_service(monkeypatch, owners="1", mode=None, whitelist=None):
    if owners is None:
        monkeypatch.delenv("LIVE_OWNER_USER_IDS", raising=False)
    else:
        monkeypatch.setenv("LIVE_OWNER_USER_IDS", owners)
    if mode is None:
        monkeypatch.delenv("LIVE_ACCESS_MODE", raising=False)
    else:
        monkeypatch.setenv("LIVE_ACCESS_MODE", mode)
    if whitelist is None:
        monkeypatch.delenv("LIVE_WHITELIST_USER_IDS", raising=False)
    else:
        monkeypatch.setenv("LIVE_WHITELIST_USER_IDS", whitelist)
    import services.live_access_control_service as svc
    return importlib.reload(svc)


def test_default_owner_only(monkeypatch):
    svc = reload_service(monkeypatch, owners="1")
    assert svc.can_user_access_live(1)["allowed"] is True
    assert svc.can_user_access_live(2)["allowed"] is False


def test_whitelist_mode(monkeypatch):
    svc = reload_service(monkeypatch, owners="1", mode="whitelist", whitelist="2")
    assert svc.can_user_access_live(1)["allowed"] is True
    assert svc.can_user_access_live(2)["allowed"] is True
    assert svc.can_user_access_live(3)["allowed"] is False


def test_everyone_mode(monkeypatch):
    svc = reload_service(monkeypatch, owners="1", mode="everyone")
    assert svc.can_user_access_live(999)["allowed"] is True


def test_disabled_mode_owner_can_test(monkeypatch):
    svc = reload_service(monkeypatch, owners="1", mode="disabled")
    assert svc.can_user_access_live(1)["allowed"] is True
    assert svc.can_user_access_live(2)["allowed"] is False


def test_malformed_env_no_crash_and_warns(monkeypatch, caplog):
    svc = reload_service(monkeypatch, owners="bad", mode="bad")
    settings = svc.get_live_access_settings()
    assert settings["mode"] == "owner_only"
    assert svc.can_user_access_live(2)["allowed"] is False
    assert "live_access_owner_ids_missing" in caplog.text


def test_denied_message_safe_words(monkeypatch):
    svc = reload_service(monkeypatch, owners="1")
    ru = svc.format_live_access_denied_message("ru")
    en = svc.format_live_access_denied_message("en")
    for text, phrases in [(ru, ["закрытой beta", "Airdrop", "DeepAlpha Points", "Soon"]), (en, ["private beta", "Airdrop", "DeepAlpha Points", "Soon"] )]:
        for phrase in phrases:
            assert phrase in text
        low = text.lower()
        for forbidden in ["token ticker", "listing", "price", "guaranteed", "profit"]:
            assert forbidden not in low


def test_denied_user_not_charged(monkeypatch):
    from services import live_analyst_service as live_svc
    charged = []
    llm = []
    monkeypatch.setattr(live_svc, "can_user_access_live", lambda user_id: {"allowed": False, "mode": "owner_only"})
    monkeypatch.setattr(live_svc, "charge_live_request", lambda *args, **kwargs: charged.append(args) or True)
    monkeypatch.setattr(live_svc, "generate_live_analyst_text", lambda *args, **kwargs: llm.append(args) or "ok")
    result = live_svc.process_live_text(2, "BTC?", ui_language="en")
    assert result["ok"] is False
    assert result["access_denied"] is True
    assert "private beta" in result["message"]
    assert charged == []
    assert llm == []


def test_admin_style_updates(monkeypatch):
    svc = reload_service(monkeypatch, owners="1")
    svc.update_live_access_settings({"mode": "everyone"})
    assert svc.get_live_access_settings()["mode"] == "everyone"
    svc.update_live_access_settings({"mode": "owner_only"})
    assert svc.get_live_access_settings()["mode"] == "owner_only"
    svc.add_live_whitelist_user(2)
    assert 2 in svc.list_live_whitelist_users()
