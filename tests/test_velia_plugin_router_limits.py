from services import velia_plugin_router
from services.velia_plugin_router import _bounded_search_query


def test_search_query_is_limited_to_brave_api_contract():
    source = " ".join(f"word{index}" for index in range(100))

    bounded = _bounded_search_query(source)

    assert len(bounded) <= 400
    assert len(bounded.split()) <= 50
    assert bounded.startswith("word0 word1")


def test_search_query_trims_empty_input():
    assert _bounded_search_query("   ") == ""


def test_ordinary_chat_skips_plugin_preferences_database_read(monkeypatch):
    preference_reads = []
    monkeypatch.setattr(
        velia_plugin_router.plugins,
        "get_user_plugins",
        lambda user_id: preference_reads.append(user_id),
    )

    result = velia_plugin_router.resolve_live_plugin_context(
        7,
        "Расскажи короткую шутку",
    )

    assert result == {
        "ok": True,
        "used": [],
        "context": "",
        "sources": [],
        "errors": [],
    }
    assert preference_reads == []


def test_live_intent_reads_preferences_after_classification(monkeypatch):
    preference_reads = []
    monkeypatch.setattr(
        velia_plugin_router.plugins,
        "get_user_plugins",
        lambda user_id: preference_reads.append(user_id) or {
            "weather": {"enabled": False},
            "web_search": {"enabled": False},
        },
    )

    result = velia_plugin_router.resolve_live_plugin_context(
        7,
        "Какая погода в Анталии?",
    )

    assert result["used"] == []
    assert preference_reads == [7]
