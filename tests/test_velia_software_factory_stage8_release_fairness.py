from __future__ import annotations

from services import velia_software_factory_rollout_service as rollout
from services import velia_software_factory_stage8_full_autonomy_service as stage8
from services import velia_software_factory_stage8_release_runtime_patch as release_runtime


def _review_ready(objective: str):
    return {
        "status": "review_ready",
        "plan": {"objective": objective},
        "integration_validation": {"status": "passed", "report": {"status": "passed"}},
    }


def test_release_candidate_window_excludes_terminal_and_rotates_retryable(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, params):
            captured["query"] = " ".join(str(query).split())
            captured["params"] = params

        def fetchall(self):
            return [(7, "exec-new")]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(release_runtime, "ensure_stage8_release_tables", lambda execution_module: None)
    monkeypatch.setattr(release_runtime, "get_connection", lambda: Connection())

    result = release_runtime._release_candidates(object(), 5)

    assert result == [(7, "exec-new")]
    assert "NOT IN ('complete','terminal_blocked')" in captured["query"]
    assert "velia_factory_stage8_release_authorization_required" in captured["query"]
    assert "ORDER BY COALESCE(s.updated_at,e.updated_at) ASC" in captured["query"]
    assert captured["params"] == (5,)


def test_protected_repository_becomes_terminal_blocker(monkeypatch):
    saved = {}

    class ExecutionModule:
        @staticmethod
        def get_execution(user_id, execution_id):
            return _review_ready("Build and deploy to production")

        @staticmethod
        def evaluate_delivery_candidate(user_id, execution_id):
            return {
                "candidate_id": "candidate-1",
                "status": "eligible",
                "release_eligible": True,
                "repositories": [{"repository_full_name": "SergeyTo95/deepalpha-bot"}],
            }

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setattr(rollout, "user_allowed", lambda user_id: True)
    monkeypatch.setattr(stage8, "execution_allowed", lambda user_id, user_eligible: True)
    monkeypatch.setattr(release_runtime, "configured_admin_id", lambda: 42)
    monkeypatch.setattr(release_runtime, "_state", lambda execution_module, user_id, execution_id: {"status": "ready"})

    def fake_save(execution_module, user_id, execution_id, **fields):
        saved.update(fields)
        return dict(fields)

    monkeypatch.setattr(release_runtime, "_save_state", fake_save)

    result = release_runtime._progress_release(ExecutionModule(), 7, "exec-1")

    assert result["status"] == "terminal_blocked"
    assert result["candidate_id"] == "candidate-1"
    assert result["blocker_code"] == "velia_factory_stage8_protected_repository_forbidden"
    assert saved["status"] == "terminal_blocked"


def test_retryable_candidate_blocker_stays_retryable(monkeypatch):
    saved = {}

    class ExecutionModule:
        @staticmethod
        def get_execution(user_id, execution_id):
            return _review_ready("Build and deploy to production")

        @staticmethod
        def evaluate_delivery_candidate(user_id, execution_id):
            return {
                "candidate_id": "candidate-retry",
                "status": "blocked",
                "release_eligible": False,
                "repositories": [],
            }

    monkeypatch.setenv("VELIA_SOFTWARE_FACTORY_ROLLOUT_MODE", "full_autonomy")
    monkeypatch.setattr(rollout, "user_allowed", lambda user_id: True)
    monkeypatch.setattr(stage8, "execution_allowed", lambda user_id, user_eligible: True)
    monkeypatch.setattr(release_runtime, "_state", lambda execution_module, user_id, execution_id: {"status": "ready"})

    def fake_save(execution_module, user_id, execution_id, **fields):
        saved.update(fields)
        return dict(fields)

    monkeypatch.setattr(release_runtime, "_save_state", fake_save)

    result = release_runtime._progress_release(ExecutionModule(), 7, "exec-2")

    assert result["status"] == "blocked"
    assert result["blocker_code"] == "velia_factory_stage8_candidate_not_eligible"
    assert saved["status"] == "blocked"
