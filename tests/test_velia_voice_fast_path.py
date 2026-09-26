from services import velia_flash_service as flash


def test_voice_bounded_history_keeps_latest_question_and_caps_context(monkeypatch):
    monkeypatch.setenv("VELIA_VOICE_CONTEXT_MESSAGES", "4")
    monkeypatch.setenv("VELIA_VOICE_CONTEXT_CHARS", "600")
    messages = [
        {"role": "user", "content": "old-user-" + "a" * 500},
        {"role": "assistant", "content": "old-assistant-" + "b" * 500},
        {"role": "user", "content": "middle-" + "c" * 300},
        {"role": "assistant", "content": "middle-answer-" + "d" * 300},
        {"role": "user", "content": "Как твой день проходит?"},
    ]

    bounded = flash._voice_bounded_history(messages)

    assert len(bounded) <= 4
    assert bounded[-1]["role"] == "user"
    assert bounded[-1]["content"] == "Как твой день проходит?"
    assert sum(len(item["content"]) for item in bounded) <= 600


def test_voice_fast_flag_is_scoped_by_feature_env(monkeypatch):
    flash._VOICE_CONTEXT.enabled = True
    try:
        monkeypatch.setenv("VELIA_VOICE_FAST_PATH_ENABLED", "true")
        assert flash._voice_fast_enabled() is True
        monkeypatch.setenv("VELIA_VOICE_FAST_PATH_ENABLED", "false")
        assert flash._voice_fast_enabled() is False
    finally:
        try:
            delattr(flash._VOICE_CONTEXT, "enabled")
        except AttributeError:
            pass
