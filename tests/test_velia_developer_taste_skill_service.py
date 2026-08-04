from services import velia_developer_taste_skill_service as taste


def test_backend_only_request_bypasses_taste_layer(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    profile = taste.classify(
        "Исправь retry PostgreSQL соединения в backend service",
        ["services/database_retry_service.py", "tests/test_database_retry_service.py"],
    )
    assert profile["active"] is False


def test_web_redesign_is_audit_first(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    profile = taste.classify(
        "Сделай редизайн frontend страницы профиля: премиально, но без потери функций",
        ["web/src/pages/Profile.tsx", "web/src/styles/profile.css"],
    )
    assert profile["active"] is True
    assert profile["mode"] == "web-redesign"
    assert profile["audit_first"] is True
    assert "premium" in profile["design_read"]
    assert 1 <= profile["design_variance"] <= 10
    assert 1 <= profile["motion_intensity"] <= 10
    assert 1 <= profile["visual_density"] <= 10


def test_android_screen_uses_android_native_mode(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    profile = taste.classify(
        "Улучши дизайн Android экрана чата на Jetpack Compose",
        ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
    )
    assert profile["active"] is True
    assert profile["platform"] == "android"
    assert profile["mode"] == "mobile-android-redesign"
    checks = taste.preflight_checks(profile)
    assert any("safe areas/insets" in item for item in checks)


def test_existing_ui_path_activates_layer_without_design_word(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    profile = taste.classify(
        "Добавь состояние загрузки и ошибки",
        ["frontend/components/AccountPanel.tsx"],
    )
    assert profile["active"] is True
    assert profile["platform"] == "web"


def test_normalize_design_clamps_model_values(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    fallback = taste.classify(
        "Создай премиальный мобильный интерфейс Android",
        ["app/src/main/java/com/velia/HomeScreen.kt"],
    )
    value = taste.normalize_design(
        {
            "read": "Android product screen for existing users",
            "system": "existing Material 3 theme",
            "variance": 999,
            "motion": -5,
            "density": "7",
        },
        fallback,
    )
    assert value["active"] is True
    assert value["variance"] == 10
    assert value["motion"] == 1
    assert value["density"] == 7
    assert value["source"].endswith(taste.UPSTREAM_COMMIT)


def test_guidance_is_compact_and_contains_core_guards(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    profile = taste.classify(
        "Редизайн мобильного Android интерфейса, чисто и доступно",
        ["app/src/main/java/com/velia/settings/SettingsScreen.kt"],
    )
    planning = taste.planning_guidance(profile)
    execution = taste.execution_guidance(profile, {"title": "Polish settings"})
    assert len(planning) < 5000
    assert len(execution) < 4000
    assert "audit step" in planning
    assert "Do not squeeze a desktop website" in planning
    assert "never hallucinate an import" in execution
    assert "reduced-motion" in execution


def test_layer_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", "false")
    profile = taste.classify(
        "Сделай редизайн frontend страницы",
        ["web/src/App.tsx"],
    )
    assert profile["active"] is False
