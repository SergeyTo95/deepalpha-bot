import json
from datetime import datetime

from services import velia_agent_runtime_service as runtime


def test_task_list_converts_database_timestamps_to_iso_strings(monkeypatch):
    created_at = datetime(2026, 8, 5, 12, 34, 56, 123456)
    updated_at = datetime(2026, 8, 5, 12, 35, 1)
    monkeypatch.setattr(
        runtime.jobs,
        "list_task_drafts",
        lambda user_id, limit: [
            {
                "draft_id": "draft-1",
                "title": "VELIA_AGENT_SMOKE_2026_08_05",
                "notes": "",
                "completed": False,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        ],
    )

    result = runtime._list_task_drafts(7, {"limit": 20})

    assert result == {
        "items": [
            {
                "draft_id": "draft-1",
                "title": "VELIA_AGENT_SMOKE_2026_08_05",
                "notes": "",
                "completed": False,
                "created_at": "2026-08-05T12:34:56.123456",
                "updated_at": "2026-08-05T12:35:01",
            }
        ]
    }
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result


def test_task_list_bounds_public_fields_and_handles_null_timestamps(monkeypatch):
    monkeypatch.setattr(
        runtime.jobs,
        "list_task_drafts",
        lambda user_id, limit: [
            {
                "draft_id": 123,
                "title": "T" * 500,
                "notes": "N" * 5000,
                "completed": 1,
                "created_at": None,
                "updated_at": "2026-08-05 12:35:01+00:00",
                "internal_secret": "must-not-leak",
            }
        ],
    )

    result = runtime._list_task_drafts(7, {"limit": 20})
    item = result["items"][0]

    assert item["draft_id"] == "123"
    assert len(item["title"]) == 300
    assert len(item["notes"]) == 4000
    assert item["completed"] is True
    assert item["created_at"] is None
    assert item["updated_at"] == "2026-08-05 12:35:01+00:00"
    assert "internal_secret" not in item
