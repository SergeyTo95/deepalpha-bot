import io
import json
import wave

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services import velia_calculator_service as calculator
from services import velia_free_api_budget_service as budget
from services import velia_free_tools_routes as routes
from services import velia_open_knowledge_service as knowledge
from services import velia_speech_service as speech
from services import web_search_service as search
from services import velia_deepalpha_research as research


@pytest.mark.parametrize("expression,expected", [("0.1 + 0.2", "3/10"), ("(1/3) * 9", "3"), ("2**-3", "1/8"), ("-(2+3)*4", "-20")])
def test_exact_arithmetic(expression, expected):
    assert calculator.calculate(expression)["exact"] == expected


@pytest.mark.parametrize("expression", ["__import__('os').system('id')", "().__class__", "open('x')", "1/0", "9**999999", "2**(2**12)", "1e999", "x+1", "[1]*999", "True+1", "1+" * 200, None])
def test_calculator_rejects_code_and_unbounded_computation(expression):
    with pytest.raises(calculator.CalculationError):
        calculator.calculate(expression)


@pytest.fixture
def free_search(monkeypatch):
    monkeypatch.setenv("VELIA_FREE_APIS_ONLY", "true")
    monkeypatch.setenv("VELIA_TAVILY_FREE_PLAN_CONFIRMED", "true")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "not-a-real-key")


@pytest.mark.parametrize("caller", [lambda: search.search_web_strict("test"), lambda: research._search("test")])
def test_every_tavily_path_stops_before_http_when_budget_exhausted(monkeypatch, free_search, caller):
    def exhausted(*args, **kwargs):
        raise budget.FreeAPIError("free_api_quota_exhausted", 429)
    monkeypatch.setattr(search, "reserve", exhausted)
    monkeypatch.setattr(search.requests, "post", lambda *a, **k: pytest.fail("HTTP after exhausted budget"))
    with pytest.raises(budget.FreeAPIError, match="free_api_quota_exhausted"):
        caller()


def test_tavily_free_request_preserves_any_language_and_never_auto_upgrades(monkeypatch, free_search):
    reservations, calls = [], []
    monkeypatch.setenv("WEB_SEARCH_TIMEOUT", "9999")
    monkeypatch.setenv("VELIA_TAVILY_MONTHLY_CREDITS", "999999")
    monkeypatch.setattr(search, "reserve", lambda *a, **kw: reservations.append((a, kw)))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b'{"results": []}'
    def post(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(search.requests, "post", post)
    assert search.search_web_strict("العلم", user_id=77) == []
    assert calls[0]["json"]["query"] == "العلم"
    assert calls[0]["json"]["search_depth"] == "basic"
    assert calls[0]["json"]["auto_parameters"] is False
    assert calls[0]["allow_redirects"] is False and calls[0]["timeout"][1] <= 15
    assert reservations[0][0][3] == 900 and reservations[0][1]["user_id"] == 77


def test_no_unverified_search_plan_or_paid_provider(monkeypatch, free_search):
    monkeypatch.delenv("VELIA_TAVILY_FREE_PLAN_CONFIRMED")
    with pytest.raises(budget.FreeAPIError, match="confirmed_free_plan"):
        search.search_web_strict("test")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "serper")
    with pytest.raises(budget.FreeAPIError, match="not_free_enabled"):
        search.search_web_strict("test")


def test_budget_database_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(budget, "get_connection", lambda: (_ for _ in ()).throw(RuntimeError("secret connection details")))
    with pytest.raises(budget.FreeAPIError) as exc:
        budget.reserve("test", "secret", 1, 10)
    assert str(exc.value) == "free_api_budget_unavailable"


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("VELIA_AZURE_SPEECH_ENABLED", "true")
    monkeypatch.setenv("AZURE_SPEECH_TIER", "F0")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "not-a-real-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    speech._VOICES.clear()


@pytest.mark.parametrize("tier", ["", "S0", "f1"])
def test_speech_refuses_non_f0_configuration(monkeypatch, azure, tier):
    monkeypatch.setenv("AZURE_SPEECH_TIER", tier)
    with pytest.raises(budget.FreeAPIError, match="speech_not_configured"):
        speech.synthesize("hello", "voice", user_id=1)


def test_speech_discovers_languages_and_escapes_ssml(monkeypatch, azure):
    calls, reservations = [], []
    def request(url, **kw):
        calls.append((url, kw))
        if url.endswith("voices/list"):
            return json.dumps([{"ShortName": "ar-EG-SalmaNeural", "Locale": "ar-EG", "VoiceType": "Neural", "Gender": "Female"},
                               {"ShortName": "xx-Bad", "Locale": "xx", "VoiceType": "Custom"}]).encode()
        return b"mp3"
    monkeypatch.setattr(speech, "_request", request)
    monkeypatch.setattr(speech, "reserve", lambda *a, **kw: reservations.append((a, kw)))
    assert len(speech.voices()) == 1
    assert speech.synthesize('<audio src="https://evil/"/> مرحبا', "ar-EG-SalmaNeural", user_id=77) == b"mp3"
    assert b"&lt;audio" in calls[-1][1]["body"]
    assert b"<audio" not in calls[-1][1]["body"]
    assert len([c for c in calls if c[0].endswith("voices/list")]) == 1
    assert reservations[0][1]["user_id"] == 77


def wav(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * int(16000 * seconds))
    return output.getvalue()


def test_stt_validates_actual_audio_and_uses_explicit_language(monkeypatch, azure):
    reservations, calls = [], []
    monkeypatch.setattr(speech, "reserve", lambda *a, **kw: reservations.append((a, kw)))
    monkeypatch.setattr(speech, "_request", lambda *a, **kw: calls.append(kw) or b'{"RecognitionStatus":"Success","DisplayText":"Bonjour"}')
    assert speech.transcribe(wav(1.1), "fr-FR", user_id=4)["text"] == "Bonjour"
    assert reservations[0][0][2] == 2 and calls[0]["params"]["language"] == "fr-FR"
    with pytest.raises(budget.FreeAPIError):
        speech.transcribe(wav(61), "fr-FR", user_id=4)
    with pytest.raises(budget.FreeAPIError):
        speech.transcribe(b"not wav", "fr-FR", user_id=4)
    assert len(reservations) == 1


@pytest.fixture
def open_apis():
    knowledge._CACHE.clear()
    knowledge._NEXT.clear()


def test_wikipedia_is_multilingual_cached_and_attributed(monkeypatch, open_apis):
    calls = []
    monkeypatch.setattr(knowledge, "_fetch", lambda url, params: calls.append((url, params)) or
                        {"query": {"search": [{"title": "量子", "snippet": '<span>科学</span>'}]}})
    result = knowledge.wikipedia_search("量子", "zh")
    assert result["results"][0]["attribution_required"] is True
    assert result["results"][0]["snippet"] == "科学"
    result["results"].clear()
    assert knowledge.wikipedia_search("量子", "zh")["results"]
    assert len(calls) == 1 and calls[0][0] == "https://zh.wikipedia.org/w/api.php"
    with pytest.raises(budget.FreeAPIError):
        knowledge.wikipedia_search("query", "en.evil.com/")


def test_crossref_returns_metadata_not_full_papers(monkeypatch, open_apis):
    monkeypatch.setattr(knowledge, "_fetch", lambda *a: {"message": {"items": [{"DOI": "10.1234/test", "title": ["Study"]}]}})
    result = knowledge.papers_search("Study")
    assert result["full_text_retrieved"] is False
    assert result["results"][0]["url"] == "https://doi.org/10.1234/test"


class Mobile:
    @staticmethod
    def _mobile_api_available(): return True
    @staticmethod
    def _require_mobile_auth(request):
        return {"user_id": 77} if request.headers.get("Authorization") == "Bearer ok" else None
    @staticmethod
    def _json_response(data, status=200): return web.json_response(data, status=status)


@pytest.mark.asyncio
async def test_utility_routes_authentication_limits_and_identity(monkeypatch):
    app = web.Application()
    routes.setup_free_tools_routes(app, Mobile)
    routes.setup_free_tools_routes(app, Mobile)
    headers = {"Authorization": "Bearer ok"}
    calls = []
    monkeypatch.setattr(speech, "synthesize", lambda text, voice, *, user_id: calls.append(user_id) or b"mp3")
    async with TestClient(TestServer(app)) as client:
        for method, path in [("get", "/status"), ("post", "/calculate"), ("post", "/search"),
                             ("post", "/knowledge/search"), ("post", "/papers/search"), ("get", "/speech/voices"),
                             ("post", "/speech/synthesize"), ("post", "/speech/transcribe")]:
            response = await getattr(client, method)(routes._PREFIX + path)
            assert response.status == 401
        response = await client.post(routes._PREFIX + "/calculate", headers=headers, json={"expression": "1/3*3"})
        assert (await response.json())["exact"] == "1"
        response = await client.post(routes._PREFIX + "/speech/synthesize", headers=headers,
                                     json={"text": "hello", "voice": "voice", "user_id": 999})
        assert response.status == 200 and calls == [77]
        assert response.headers["Cache-Control"] == "no-store"
        response = await client.post(routes._PREFIX + "/calculate", headers=headers, data=b"x" * 17000)
        assert response.status == 413
        response = await client.post(routes._PREFIX + "/calculate", headers=headers, json=[])
        assert response.status == 400
