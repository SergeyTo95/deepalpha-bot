from services import llm_service


class Resp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text

    def json(self):
        return {}


def test_classify_permission_denied():
    assert llm_service.classify_gemini_error(403, "PERMISSION_DENIED project has been denied access") == "permission_denied"


def test_classify_quota_exceeded():
    assert llm_service.classify_gemini_error(429, "You exceeded your current quota") == "quota_exceeded"


def test_permission_denied_stops_model_cascade(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_service, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(llm_service, "RETRY_DELAYS", [0, 0, 0])
    llm_service.clear_gemini_provider_cooldown()

    def fake_post(url, **kwargs):
        calls.append(url)
        return Resp(403, '{"error":{"status":"PERMISSION_DENIED","message":"project has been denied access"}}')

    monkeypatch.setattr(llm_service.requests, "post", fake_post)
    result = llm_service.generate_text("hello")
    assert getattr(result, "provider_error", None) == "permission_denied"
    assert len(calls) == 1
    llm_service.clear_gemini_provider_cooldown()
