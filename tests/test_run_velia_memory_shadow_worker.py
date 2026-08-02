import logging

from run_velia_memory_shadow_worker import configure_logging, worker_disabled_reason


def test_worker_is_disabled_by_default():
    assert worker_disabled_reason({}) == "VELIA_MEMORY_SHADOW_ENABLED=false"


def test_worker_runs_only_on_production_branch_by_default():
    env = {
        "VELIA_MEMORY_SHADOW_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }

    assert worker_disabled_reason(env) is None


def test_worker_refuses_preview_environment():
    env = {
        "VELIA_MEMORY_SHADOW_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "RAILWAY_GIT_BRANCH": "feature/turbo-short-term-btc",
    }

    assert worker_disabled_reason(env) == "non_production_environment:preview"


def test_worker_refuses_non_production_branch():
    env = {
        "VELIA_MEMORY_SHADOW_ENABLED": "true",
        "RAILWAY_ENVIRONMENT_NAME": "production",
        "RAILWAY_GIT_BRANCH": "feature/other",
    }

    assert worker_disabled_reason(env) == "non_production_branch:feature/other"


def test_preview_override_is_explicit():
    env = {
        "VELIA_MEMORY_SHADOW_ENABLED": "true",
        "VELIA_MEMORY_SHADOW_ALLOW_PREVIEW": "true",
        "RAILWAY_ENVIRONMENT_NAME": "preview",
        "RAILWAY_GIT_BRANCH": "feature/other",
    }

    assert worker_disabled_reason(env) is None


def test_worker_configures_info_logging_to_stdout_by_default(monkeypatch):
    captured = {}

    def fake_basic_config(**kwargs):
        captured.update(kwargs)

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    configure_logging()

    assert captured["level"] == logging.INFO
    assert captured["force"] is True
    assert captured["format"] == "%(asctime)s %(levelname)s:%(name)s:%(message)s"


def test_worker_honors_valid_log_level(monkeypatch):
    captured = {}
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setattr(
        logging,
        "basicConfig",
        lambda **kwargs: captured.update(kwargs),
    )

    configure_logging()

    assert captured["level"] == logging.DEBUG
