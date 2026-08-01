from services import velia_live_plugins_patch
from services import velia_plugin_router
from services import velia_plugin_service


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def enabled_plugins(**overrides):
    values = {
        key: {"enabled": True, "available": True}
        for key in velia_plugin_service.PLUGIN_KEYS
    }
    for key, enabled in overrides.items():
        values[key] = {"enabled": enabled, "available": True}
    return values


def test_identity_contract_fixes_public_name_and_core():
    prompt = velia_live_plugins_patch._IDENTITY_CONTRACT

    assert "You are VELIA" in prompt
    assert "You operate on Velyon Core" in prompt
    assert "Never say that you cannot determine your identity" in prompt
    assert "Never expose or mention external model vendors" in prompt


def test_normalizes_common_russian_location_inflection():
    assert (
        velia_live_plugins_patch._normalized_live_query(
            "Какая погода в Анталии сейчас?"
        )
        == "Какая погода в Antalya сейчас?"
    )


def test_extracts_turkish_location_before_postpositions():
    assert velia_plugin_service._extract_location("Antalya için hava nasıl?") == "Antalya"
    assert velia_plugin_service._extract_location("Antalya'da hava nasıl?") == "Antalya"
    assert velia_plugin_service._extract_location("İstanbul'da sıcaklık kaç?") == "İstanbul"


def test_weather_plugin_returns_live_context(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "geocoding-api" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "Antalya",
                            "admin1": "Antalya",
                            "country": "Türkiye",
                            "latitude": 36.8841,
                            "longitude": 30.7056,
                        }
                    ]
                }
            )
        return FakeResponse(
            {
                "timezone": "Europe/Istanbul",
                "current": {
                    "time": "2026-08-01T16:45",
                    "temperature_2m": 34.2,
                    "apparent_temperature": 38.1,
                    "relative_humidity_2m": 48,
                    "precipitation": 0.0,
                    "weather_code": 0,
                    "wind_speed_10m": 14.1,
                    "wind_gusts_10m": 28.0,
                },
                "daily": {
                    "time": ["2026-08-01", "2026-08-02"],
                    "weather_code": [0, 1],
                    "temperature_2m_max": [35.0, 34.0],
                    "temperature_2m_min": [26.0, 25.0],
                    "precipitation_probability_max": [0, 5],
                },
            }
        )

    monkeypatch.setattr(velia_plugin_service.requests, "get", fake_get)

    result = velia_plugin_service._weather_context(
        "Какая погода в Antalya сейчас?"
    )

    assert result["ok"] is True
    assert result["plugin"] == "weather"
    assert "Antalya, Antalya, Türkiye" in result["context"]
    assert "34.2 °C" in result["context"]
    assert "Europe/Istanbul" in result["context"]
    assert result["sources"][0]["url"] == "https://open-meteo.com/"
    assert len(calls) == 2


def test_weather_router_respects_user_toggle(monkeypatch):
    monkeypatch.setattr(
        velia_plugin_service,
        "get_user_plugins",
        lambda user_id: enabled_plugins(weather=False),
    )

    result = velia_plugin_router.resolve_live_plugin_context(
        1,
        "Какая погода в Antalya сейчас?",
    )

    assert result["ok"] is True
    assert result["used"] == []
    assert result["context"] == ""


def test_temporal_words_alone_do_not_trigger_external_search(monkeypatch):
    monkeypatch.setattr(
        velia_plugin_service,
        "get_user_plugins",
        lambda user_id: enabled_plugins(),
    )
    reserved = []
    monkeypatch.setattr(
        velia_plugin_service,
        "_reserve_plugin_call",
        lambda user_id, plugin_key: reserved.append(plugin_key) or True,
    )

    for message in (
        "Я устал сегодня, что делать?",
        "Расскажи, что сейчас лучше приготовить",
        "I feel tired today",
        "What should I do right now?",
    ):
        result = velia_plugin_router.resolve_live_plugin_context(1, message)
        assert result["used"] == []
        assert result["context"] == ""

    assert reserved == []


def test_news_noun_or_explicit_search_triggers_web_tool(monkeypatch):
    monkeypatch.setattr(
        velia_plugin_service,
        "get_user_plugins",
        lambda user_id: enabled_plugins(),
    )
    monkeypatch.setattr(
        velia_plugin_service,
        "_reserve_plugin_call",
        lambda user_id, plugin_key: True,
    )
    monkeypatch.setattr(
        velia_plugin_service,
        "_google_news_context",
        lambda message: {
            "ok": True,
            "context": "Latest headlines",
            "sources": [{"title": "Source", "url": "https://example.com/news"}],
        },
    )
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    result = velia_plugin_router.resolve_live_plugin_context(
        1,
        "Последние новости про искусственный интеллект",
    )

    assert result["used"] == ["web_search"]
    assert "Latest headlines" in result["context"]


def test_weather_router_uses_plugin_when_enabled(monkeypatch):
    monkeypatch.setattr(
        velia_plugin_service,
        "get_user_plugins",
        lambda user_id: enabled_plugins(),
    )
    monkeypatch.setattr(
        velia_plugin_service,
        "_reserve_plugin_call",
        lambda user_id, plugin_key: True,
    )
    monkeypatch.setattr(
        velia_plugin_service,
        "_weather_context",
        lambda message: {
            "ok": True,
            "plugin": "weather",
            "context": "Temperature: 34 °C",
            "sources": [{"title": "Open-Meteo", "url": "https://open-meteo.com/"}],
        },
    )

    result = velia_plugin_router.resolve_live_plugin_context(
        5811340792,
        "Какая погода в Antalya сейчас?",
    )

    assert result["ok"] is True
    assert result["used"] == ["weather"]
    assert "34 °C" in result["context"]
    assert result["retrieved_at"].endswith("Z")


def test_tool_failure_forces_honest_answer_context():
    prompt = velia_plugin_service.plugin_context_for_prompt(
        {
            "ok": False,
            "context": "",
            "errors": ["plugin_timeout"],
        }
    )

    assert "could not complete" in prompt
    assert "do not invent current data" in prompt
