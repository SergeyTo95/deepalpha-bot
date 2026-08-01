from types import SimpleNamespace

from services.velia_weather_location_patch import (
    install,
    location_fallback_candidates,
)


def test_novopolotsk_inflection_prefers_known_ascii_alias():
    candidates = location_fallback_candidates("Новополоцке")

    assert candidates[0] == "Novopolotsk"
    assert "Новополоцк" in candidates


def test_common_russian_city_forms_are_normalized():
    assert location_fallback_candidates("Минске")[0] == "Minsk"
    assert location_fallback_candidates("Москве")[0] == "Moscow"
    assert location_fallback_candidates("Полоцке")[0] == "Polotsk"
    assert location_fallback_candidates("Анталии")[0] == "Antalya"


def test_patch_retries_failed_geocoding_with_normalized_candidate():
    calls = []

    def extract_location(message):
        return "Новополоцке" if "Новополоцке" in message else "Novopolotsk"

    def weather_context(message):
        calls.append(message)
        if "Novopolotsk" in message:
            return {
                "ok": True,
                "plugin": "weather",
                "context": "Location: Novopolotsk, Belarus\nTemperature: 19 °C",
                "sources": [],
            }
        return {"ok": False, "error": "weather_location_not_found"}

    module = SimpleNamespace(
        _weather_context=weather_context,
        _extract_location=extract_location,
    )

    install(module)
    result = module._weather_context("Какая погода в Новополоцке?")

    assert result["ok"] is True
    assert result["location_resolution"] == {
        "requested": "Новополоцке",
        "resolved_query": "Novopolotsk",
    }
    assert calls == [
        "Какая погода в Новополоцке?",
        "weather in Novopolotsk now",
    ]


def test_patch_does_not_retry_non_location_failures():
    calls = []

    def weather_context(message):
        calls.append(message)
        return {"ok": False, "error": "plugin_timeout"}

    module = SimpleNamespace(
        _weather_context=weather_context,
        _extract_location=lambda message: "Новополоцке",
    )

    install(module)
    result = module._weather_context("Какая погода в Новополоцке?")

    assert result == {"ok": False, "error": "plugin_timeout"}
    assert calls == ["Какая погода в Новополоцке?"]
