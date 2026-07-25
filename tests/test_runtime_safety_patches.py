from types import SimpleNamespace

from agents.runtime_safety_patches import install_news_agent_runtime_safety


def test_news_runtime_patch_defines_missing_context_globals():
    module = SimpleNamespace(
        _score_source=lambda item, entities, question, deadline, event_drivers: event_drivers
    )

    install_news_agent_runtime_safety(module)

    assert module.is_background is False
    assert module.cycle_id is None
    assert module.job_id is None
    assert module.request_id is None


def test_news_runtime_patch_normalizes_null_must_find():
    module = SimpleNamespace(
        _score_source=lambda item, entities, question, deadline, event_drivers: event_drivers[
            "must_find"
        ]
    )

    install_news_agent_runtime_safety(module)

    result = module._score_source({}, [], "question", "", {"must_find": None})

    assert result == []
