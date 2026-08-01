from services import velia_chat_service
from services.http_security_service import _is_api_path


def test_generate_conversation_title_uses_first_six_words():
    assert (
        velia_chat_service.generate_conversation_title(
            "помоги составить подробный план запуска мобильного приложения завтра"
        )
        == "Помоги составить подробный план запуска мобильного"
    )


def test_generate_conversation_title_normalizes_whitespace_and_punctuation():
    assert velia_chat_service.generate_conversation_title("  анализ   рынка Bitcoin?  ") == "Анализ рынка Bitcoin"


def test_generate_conversation_title_has_safe_fallback():
    assert velia_chat_service.generate_conversation_title("   ", "Новый чат") == "Новый чат"


def test_chat_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VELIA_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("VELIA_CHAT_BETA_USER_IDS", raising=False)
    assert velia_chat_service.is_velia_chat_enabled_for_user(1) is False


def test_beta_allowlist_restricts_chat(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ENABLED", "true")
    monkeypatch.setenv("VELIA_CHAT_BETA_USER_IDS", "10, 20")
    assert velia_chat_service.is_velia_chat_enabled_for_user(10) is True
    assert velia_chat_service.is_velia_chat_enabled_for_user(11) is False


def test_empty_allowlist_allows_enabled_users(monkeypatch):
    monkeypatch.setenv("VELIA_CHAT_ENABLED", "true")
    monkeypatch.setenv("VELIA_CHAT_BETA_USER_IDS", "")
    assert velia_chat_service.is_velia_chat_enabled_for_user(999) is True


def test_shared_http_security_recognizes_mobile_api_paths():
    assert _is_api_path("/mobile-api/v1/me") is True
    assert _is_api_path("/mobile-api/v1/conversations") is True
    assert _is_api_path("/mobile-connect") is False
