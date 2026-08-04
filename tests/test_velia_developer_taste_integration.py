from services import velia_developer_coding_service as coding
from services import velia_developer_fast_path_service as fast_path
from services import velia_developer_taste_skill_service as taste


def _project():
    return {
        "repository_full_name": "SergeyTo95/deepalpha-android",
        "selected_branch": "main",
    }


def test_ui_plan_prompt_includes_compact_taste_guidance(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    goal = "Сделай редизайн Android экрана профиля на Jetpack Compose"
    paths = ["app/src/main/java/com/velia/profile/ProfileScreen.kt"]
    profile = taste.classify(goal, paths)
    prompt = coding._plan_prompt(
        _project(),
        goal,
        paths,
        "FILE ProfileScreen.kt\n1: @Composable",
        taste_profile=profile,
    )
    assert "VELIA DESIGN TASTE" in prompt
    assert '"design"' in prompt
    assert "audit step" in prompt
    assert len(prompt) < 26000


def test_backend_plan_prompt_does_not_include_taste_context(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    goal = "Исправь PostgreSQL retry в backend service"
    paths = ["services/database_retry_service.py"]
    profile = taste.classify(goal, paths)
    prompt = coding._plan_prompt(
        _project(),
        goal,
        paths,
        "FILE services/database_retry_service.py",
        taste_profile=profile,
    )
    assert profile["active"] is False
    assert "VELIA DESIGN TASTE" not in prompt
    assert '"design"' not in prompt


def test_normalized_plan_keeps_server_bounded_design_profile(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    fallback = taste.classify(
        "Создай премиальный Android экран настроек",
        ["app/src/main/java/com/velia/SettingsScreen.kt"],
    )
    plan = coding._normalize_plan(
        {
            "title": "Settings polish",
            "summary": "Improve settings hierarchy",
            "design": {
                "mode": "backend-admin",
                "platform": "server",
                "read": "Premium Android settings for existing users",
                "system": "Material 3",
                "variance": 100,
                "motion": 0,
                "density": 6,
            },
            "steps": [
                {
                    "title": "Polish settings",
                    "objective": "Improve hierarchy and states",
                    "files": ["app/src/main/java/com/velia/SettingsScreen.kt"],
                    "checks": ["Compose tests"],
                }
            ],
        },
        design_profile=fallback,
    )
    assert plan["design"]["active"] is True
    assert plan["design"]["variance"] == 10
    assert plan["design"]["motion"] == 1
    assert plan["design"]["platform"] == "android"
    assert plan["design"]["mode"] == "mobile-android"


def test_step_prompt_adds_preflight_without_extra_model_stage(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    fallback = taste.classify(
        "Редизайн Android экрана чата",
        ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
    )
    design = taste.normalize_design({}, fallback)
    job = {
        "goal": "Редизайн Android экрана чата",
        "base_branch": "main",
        "work_branch": "velia/chat-redesign",
        "total_steps": 1,
        "plan": {"design": design},
    }
    step = {
        "index": 1,
        "title": "Polish chat screen",
        "objective": "Improve hierarchy and interaction states",
        "files": ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
        "checks": ["Run Compose tests"],
    }
    prompt = coding._step_prompt(_project(), job, step, "FILE ChatScreen.kt\n1: @Composable")
    assert "DESIGN EXECUTION GUARD" in prompt
    assert "Android safe areas/insets" in prompt
    assert prompt.count("Return ONLY one compact JSON object") == 1


def test_taste_context_caps_keep_default_prompts_under_cost_limits(monkeypatch):
    for name in (
        "VELIA_DEVELOPER_TASTE_PLAN_EVIDENCE_CHARS",
        "VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS",
        "KIMI_INPUT_USD_PER_MTOK",
        "KIMI_OUTPUT_USD_PER_MTOK",
    ):
        monkeypatch.delenv(name, raising=False)
    fallback = taste.classify(
        "Сделай премиальный редизайн Android экрана чата",
        ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
    )
    evidence = coding._design_plan_evidence(("1: @Composable chat content\n" * 1500), fallback)
    plan_prompt = coding._plan_prompt(
        _project(),
        "Сделай премиальный редизайн Android экрана чата",
        ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
        evidence,
        taste_profile=fallback,
    )
    assert len(evidence) <= 10000
    assert fast_path._estimate_cost(plan_prompt, 1400) <= 0.04

    design = taste.normalize_design({}, fallback)
    job = {
        "goal": "Сделай премиальный редизайн Android экрана чата",
        "base_branch": "main",
        "work_branch": "velia/chat-redesign",
        "total_steps": 1,
        "plan": {"design": design},
    }
    step = {
        "index": 1,
        "title": "Polish chat screen",
        "objective": "Improve hierarchy and interaction states",
        "files": ["app/src/main/java/com/velia/chat/ChatScreen.kt"],
        "checks": ["Run Compose tests"],
    }
    context = coding._design_step_context(("1: @Composable chat content\n" * 2500), design)
    step_prompt = coding._step_prompt(_project(), job, step, context)
    assert len(context) <= 17000
    assert fast_path._estimate_cost(step_prompt, 2400) <= 0.06


def test_backend_context_is_not_truncated(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_STEP_CONTEXT_CHARS", raising=False)
    raw = "backend-context-" * 3000
    assert coding._design_step_context(raw, {"active": False}) == raw


def test_formatted_plan_surfaces_design_read(monkeypatch):
    monkeypatch.delenv("VELIA_DEVELOPER_TASTE_SKILL_ENABLED", raising=False)
    fallback = taste.classify(
        "Сделай премиальный редизайн frontend профиля",
        ["web/src/Profile.tsx"],
    )
    design = taste.normalize_design({}, fallback)
    text = coding.format_plan(
        {
            "plan": {
                "summary": "Improve profile UI",
                "design": design,
                "steps": [
                    {
                        "index": 1,
                        "title": "Audit and polish",
                        "objective": "Audit and improve profile",
                        "files": ["web/src/Profile.tsx"],
                        "checks": [],
                    }
                ],
                "suggestions": [],
            }
        },
        "Сделай редизайн",
    )
    assert "### Направление дизайна" in text
    assert "вариативность" in text
    assert "Выполняй план" in text
