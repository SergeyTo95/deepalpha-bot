from services import velia_developer_chat_presentation_service as presentation


def _planned_job():
    return {
        "job_id": "job-1",
        "goal": "Добавить документацию",
        "status": "planned",
        "base_branch": "feature/turbo-short-term-btc",
        "work_branch": "",
        "current_step": 0,
        "total_steps": 2,
        "plan": {
            "summary": "Добавить короткий документ и проверить его согласованность.",
            "steps": [
                {
                    "index": 1,
                    "title": "Создать документ",
                    "objective": "Добавить smoke overview.",
                    "files": ["docs/velia-autopilot-smoke.md"],
                    "checks": ["Review Markdown structure"],
                },
                {
                    "index": 2,
                    "title": "Сверить ограничения",
                    "objective": "Сравнить с основной спецификацией.",
                    "files": ["docs/velia-autopilot-smoke.md"],
                    "checks": ["Compare documented limits"],
                },
            ],
            "suggestions": ["Добавить ссылку из README позже"],
        },
        "step_results": [],
        "estimated_cost_usd": 0.015,
    }


def test_plan_presentation_is_bounded_and_approval_gated():
    result = {
        "provider": "velia_coding_agent",
        "reason": "developer_coding_plan_ready",
        "text": "План готов",
        "developer_context": {
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "selected_branch": "feature/turbo-short-term-btc",
        },
    }

    value = presentation.build_presentation(
        result=result,
        job=_planned_job(),
        message="Создай файл документации",
    )

    assert value["schema_version"] == 2
    assert value["kind"] == "coding_plan"
    assert value["can_execute"] is True
    assert value["execute_command"] == "Выполняй план"
    assert value["coding"]["repository_full_name"] == "SergeyTo95/deepalpha-bot"
    assert value["coding"]["base_branch"] == "feature/turbo-short-term-btc"
    assert value["coding"]["steps"][0]["files"] == ["docs/velia-autopilot-smoke.md"]
    assert value["coding"]["draft_pr_only"] is True
    assert value["coding"]["auto_merge"] is False
    assert value["coding"]["deployment"] is False


def test_completed_presentation_exposes_only_safe_github_pr_url():
    job = _planned_job()
    job.update(
        {
            "status": "completed",
            "work_branch": "velia/docs-smoke",
            "pull_request_number": 406,
            "pull_request_url": "https://evil.example/pr/406",
            "current_step": 2,
            "step_results": [
                {
                    "index": 1,
                    "title": "Создать документ",
                    "summary": "Документ создан.",
                    "files": ["docs/velia-autopilot-smoke.md"],
                    "checks": ["Markdown reviewed"],
                    "commit_sha": "2613f3237e756bd03996881777f93a2e732881e2",
                }
            ],
        }
    )
    result = {
        "provider": "velia_coding_agent",
        "reason": "developer_coding_completed",
        "text": "Готово",
        "estimated_cost_usd": 0.02,
        "developer_context": {
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "selected_branch": "feature/turbo-short-term-btc",
            "work_branch": "velia/docs-smoke",
            "pull_request": {
                "number": 406,
                "url": "https://github.com/SergeyTo95/deepalpha-bot/pull/406",
            },
        },
    }

    value = presentation.build_presentation(result=result, job=job, message="Выполняй план")

    assert value["kind"] == "coding_completed"
    assert value["can_execute"] is False
    assert value["coding"]["pull_request"] == {
        "number": 406,
        "url": "https://github.com/SergeyTo95/deepalpha-bot/pull/406",
        "draft": True,
    }
    assert value["coding"]["steps"][0]["commit_sha"].startswith("2613f323")

    result["developer_context"]["pull_request"]["url"] = "javascript:alert(1)"
    unsafe = presentation.build_presentation(result=result, job=job, message="Выполняй план")
    assert unsafe["coding"]["pull_request"]["url"] == ""


def test_enrichment_persists_context_best_effort(monkeypatch):
    monkeypatch.setattr(presentation, "_latest_job", lambda user_id, conversation_id: _planned_job())
    captured = []
    monkeypatch.setattr(
        presentation.presentation_store,
        "persist_context_best_effort",
        lambda **kwargs: captured.append(kwargs),
    )
    result = {
        "provider": "velia_coding_agent",
        "reason": "developer_coding_plan_ready",
        "request_id": "request-1",
        "text": "План готов",
        "developer_context": {
            "repository_full_name": "SergeyTo95/deepalpha-bot",
            "selected_branch": "feature/turbo-short-term-btc",
        },
    }

    enriched = presentation.enrich_result(
        result,
        user_id=7,
        conversation_id="conversation-1",
        request_id="request-1",
        message="Добавь документацию",
    )

    assert enriched["agent_context"]["presentation"]["kind"] == "coding_plan"
    assert captured[0]["request_id"] == "request-1"
    assert captured[0]["context"]["presentation"]["schema_version"] == 2


def test_compact_progress_hides_long_branch_and_bounds_steps():
    assert presentation.compact_progress_text(
        "Создаю рабочую ветку velia/20260805-1617-docs-add-very-long-name…"
    ) == "Создаю изолированную рабочую ветку…"
    assert presentation.compact_progress_text(
        "Задача 1/2: Add smoke documentation file — анализирую файлы…"
    ) == "Шаг 1/2 · Add smoke documentation file"
    assert presentation.compact_progress_text(
        "Задача 1/2 завершена, commit 2613f323. Перехожу дальше…"
    ) == "Шаг 1/2 завершён · 2613f323"
