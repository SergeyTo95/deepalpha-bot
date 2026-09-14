import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from services import velia_media_prompt_cache as cache
from services import velia_media_worker_client as worker
from services.velia_repository_context_security import redact_source, sensitive_context_path
from test_velia_media_worker_client import FakeResponse, _configure


@pytest.fixture(autouse=True)
def empty_prompt_cache(monkeypatch):
    monkeypatch.setenv("VELIA_MEDIA_PROMPT_CACHE_ENABLED", "true")
    with cache._LOCK:
        cache._CACHE.clear()
        cache._PENDING.clear()
    yield
    with cache._LOCK:
        cache._CACHE.clear()
        cache._PENDING.clear()


def test_cache_is_tenant_and_instruction_scoped():
    calls = []
    def rewrite(user_id=1, instruction="five-second scene"):
        return cache.reuse_media_text(
            user_id=user_id, instruction=instruction, minimum_chars=8,
            producer=lambda: calls.append(True) or "A coherent cinematic scene",
        )
    assert rewrite() == rewrite()
    rewrite(user_id=2)
    rewrite(instruction="fifteen-second scene")
    assert len(calls) == 3


def test_concurrent_rewrites_only_call_provider_once():
    entered, release = threading.Event(), threading.Event()
    calls = []
    def producer():
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return "A coherent cinematic scene"
    def rewrite():
        return cache.reuse_media_text(user_id=7, instruction="scene", producer=producer, minimum_chars=8)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(rewrite) for _ in range(4)]
        assert entered.wait(5)
        release.set()
        assert {f.result(timeout=5) for f in futures} == {"A coherent cinematic scene"}
    assert len(calls) == 1


def test_failed_or_empty_rewrites_are_not_cached():
    calls = []
    def producer():
        calls.append(True)
        return ""
    for _ in range(2):
        assert cache.reuse_media_text(user_id=1, instruction="scene", producer=producer, minimum_chars=8) == ""
    assert len(calls) == 2
    with pytest.raises(ValueError):
        cache.reuse_media_text(
            user_id=1, instruction="scene", minimum_chars=8,
            producer=lambda: (_ for _ in ()).throw(ValueError("provider failed")),
        )
    assert not cache._PENDING and not cache._CACHE


def test_cache_expires_and_model_configuration_invalidates(monkeypatch):
    now, calls = [10], []
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: now[0]))
    def rewrite():
        return cache.reuse_media_text(
            user_id=1, instruction="scene", minimum_chars=8,
            producer=lambda: calls.append(True) or "A coherent cinematic scene",
        )
    rewrite()
    now[0] += 601
    rewrite()
    monkeypatch.setenv("KIMI_MODEL", "another-model")
    rewrite()
    assert len(calls) == 3


def test_media_budget_is_lower_without_reducing_coding_reasoning(monkeypatch):
    from services import kimi_gateway as kimi
    from test_kimi_provider import _enable_kimi, _install_fake_db, _FakeResponse, _success_payload

    _enable_kimi(monkeypatch)
    _install_fake_db(monkeypatch)
    monkeypatch.setenv("KIMI_MAX_COMPLETION_TOKENS", "8192")
    monkeypatch.setenv("KIMI_REASONING_EFFORT", "high")
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return _FakeResponse(200, _success_payload())
    monkeypatch.setattr(kimi.requests, "post", post)
    assert kimi.call_kimi(prompt="Rewrite", feature="studio_video_prompt")["ok"]
    assert kimi.call_kimi(prompt="Review", feature="velia_developer_review")["ok"]
    assert calls[0]["json"]["max_completion_tokens"] == 3072
    assert calls[0]["json"]["reasoning_effort"] == "low"
    assert calls[1]["json"]["max_completion_tokens"] == 8192
    assert calls[1]["json"]["reasoning_effort"] == "high"
    assert all(call["allow_redirects"] is False for call in calls)


@pytest.mark.parametrize("status", [302, 307])
def test_worker_redirect_is_rejected_and_closed(monkeypatch, status):
    _configure(monkeypatch)
    response = FakeResponse(status_code=status)
    def request(*args, **kwargs):
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        return response
    monkeypatch.setattr(worker.requests, "request", request)
    with pytest.raises(worker.MediaWorkerError, match="redirect_rejected"):
        worker._json_request("GET", "/v1/jobs/job-1234", headers={})
    assert response.closed


def test_worker_json_limit_closes_response(monkeypatch):
    _configure(monkeypatch)
    response = FakeResponse(content=b"x" * (256 * 1024 + 1))
    monkeypatch.setattr(worker.requests, "request", lambda *a, **kw: response)
    with pytest.raises(worker.MediaWorkerError, match="response_too_large"):
        worker._json_request("GET", "/v1/jobs/job-1234", headers={})
    assert response.closed


def test_broken_download_closes_response(monkeypatch):
    _configure(monkeypatch)
    class BrokenResponse(FakeResponse):
        def iter_content(self, **kwargs):
            yield b"x"
            raise worker.requests.ConnectionError("interrupted")
    response = BrokenResponse()
    monkeypatch.setattr(worker.requests, "get", lambda *a, **kw: response)
    with pytest.raises(worker.MediaWorkerError, match="download_failed"):
        worker._download_artifact(
            job_id="job-1234", request_id="request",
            descriptor={"id": "artifact-1", "size_bytes": 10, "sha256": "a" * 64, "media_type": "image/png"},
        )
    assert response.closed


@pytest.mark.parametrize("artifact_id", ["../private", "abc?token=1", "a/b", "%2e%2e", "a" * 129])
def test_artifact_identifier_cannot_change_path(artifact_id):
    with pytest.raises(worker.MediaWorkerError, match="artifact_invalid"):
        worker._artifact_descriptor({"artifact": {
            "id": artifact_id, "media_type": "image/png", "size_bytes": 1, "sha256": "a" * 64,
        }})


@pytest.mark.parametrize("path", [".env.staging", "config/service_account.json", "certs/server.key", ".aws/config"])
def test_sensitive_read_and_write_paths_fail_before_fetch(monkeypatch, path):
    from services import velia_developer_github_service as github
    from services import velia_developer_github_write_service as write

    assert sensitive_context_path(path)
    assert write._protected_path(path)
    monkeypatch.setattr(github, "_installation_token", lambda *a: pytest.fail("must reject before GitHub access"))
    with pytest.raises(github.DeveloperGithubError) as exc:
        github.read_file(1, 1, "owner/repo", "main", path)
    assert exc.value.status == 403
    with pytest.raises(write.DeveloperWriteError) as exc:
        write.read_utf8_file({}, "velia/work", path)
    assert exc.value.status == 403


def test_redaction_preserves_line_numbers_and_environment_lookups():
    source = 'API_KEY = "super-private-value"\nPASSWORD = os.getenv("PASSWORD")\nurl = "postgres://user:password@host/db"\n'
    redacted = redact_source(source)
    assert "super-private-value" not in redacted and "user:password" not in redacted
    assert 'os.getenv("PASSWORD")' in redacted
    assert len(redacted.splitlines()) == len(source.splitlines())
    assert not sensitive_context_path("config/.env.example")
    key = "-----BEGIN PRIVATE KEY-----\nprivate-base64\n-----END PRIVATE KEY-----"
    assert "private-base64" not in redact_source(key)
    assert len(redact_source(key).splitlines()) == 3


def test_duplicate_code_windows_do_not_consume_context_budget():
    from services import velia_developer_fast_path_service as fast
    item = {"path": "app.py", "sha": "a" * 40, "start_line": 1, "end_line": 2,
            "content": '1: API_KEY = "super-private-value"\n2: useful = True'}
    evidence, visible, ranges = fast._pack_evidence([item, dict(item)], 20000)
    assert evidence.count("FILE app.py") == 1 and len(visible) == 1
    assert "super-private-value" not in evidence
    assert ranges == {"app.py": [(1, 2)]}


def test_instrumental_prompt_respects_duration_without_requesting_vocals(monkeypatch):
    from services import llm_service
    from services import velia_studio_music_prompt_service as music
    calls = []
    monkeypatch.setattr(llm_service, "generate_music_text", lambda instruction, **kw: calls.append(instruction) or "An instrumental piano arrangement")
    result = music._rewrite_prompt(
        "Piano composition", user_id=1, generation_id="generation", session_id="session",
        duration_seconds=120, instrumental=True,
    )
    assert result == "An instrumental piano arrangement"
    assert "120 seconds" in calls[0] and "no singing, speech or vocal layers" in calls[0]
    assert "natural vocals" not in calls[0]
