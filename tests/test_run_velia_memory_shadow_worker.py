from run_velia_memory_shadow_worker import worker_disabled_reason


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
