from __future__ import annotations

from pathlib import Path

from services import velia_agent_coding_autopilot_ci_reliability_patch as patch
from services import velia_agent_coding_autopilot_ci_service as ci


def test_reliability_patch_is_installed_before_ci_worker_patch():
    source = Path("services/velia_agent_coding_autopilot_ci_routes.py").read_text(
        encoding="utf-8"
    )
    reliability_index = source.index("ci_reliability.install()")
    ci_index = source.index("ci_service.install_ci_repair_loop()")
    assert reliability_index < ci_index


def test_disabled_ci_does_not_install_reliability_or_touch_database():
    source = Path("services/velia_agent_coding_autopilot_ci_routes.py").read_text(
        encoding="utf-8"
    )
    assert "if ci_service.ci_watch_enabled():" in source
    assert source.index("if ci_service.ci_watch_enabled():") < source.index(
        "ci_reliability.install()"
    )


def test_ci_claim_uses_database_lease_and_skip_locked():
    source = Path(
        "services/velia_agent_coding_autopilot_ci_reliability_patch.py"
    ).read_text(encoding="utf-8")
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "AND claimed_until<=%s" in source
    assert "SET claimed_by=%s,claimed_until=%s" in source
    assert 'claim_id = f"ci:{uuid.uuid4()}"' in source
    assert "VELIA_DEVELOPER_AUTOPILOT_CI_CLAIM_SECONDS" in source


def test_pending_poll_and_repair_use_different_leases():
    source = Path(
        "services/velia_agent_coding_autopilot_ci_reliability_patch.py"
    ).read_text(encoding="utf-8")
    assert "VELIA_DEVELOPER_AUTOPILOT_CI_POLL_SECONDS" in source
    assert "VELIA_DEVELOPER_AUTOPILOT_LEASE_SECONDS" in source
    assert 'status == "waiting_ci"' in source
    assert 'status == "repairing"' in source


def test_repair_cost_is_persisted_on_run_column():
    source = Path(
        "services/velia_agent_coding_autopilot_ci_reliability_patch.py"
    ).read_text(encoding="utf-8")
    assert "estimated_cost_usd=CASE WHEN %s>0 THEN %s ELSE estimated_cost_usd END" in source
    assert 'payload.get("estimated_cost_usd")' in source


def test_active_attempt_clears_stale_finished_timestamp():
    source = Path(
        "services/velia_agent_coding_autopilot_ci_reliability_patch.py"
    ).read_text(encoding="utf-8")
    assert 'status not in {"waiting", "pending", "repairing"}' in source
    assert "SET finished_at=NULL WHERE attempt_id=%s" in source


def test_install_is_idempotent(monkeypatch):
    monkeypatch.setattr(patch, "_INSTALLED", False)
    original_set_run_state = ci._set_run_state
    original_set_attempt = ci._set_attempt
    original_claim = ci._claim_ci_run
    patch.install()
    first_set_run_state = ci._set_run_state
    first_set_attempt = ci._set_attempt
    first_claim = ci._claim_ci_run
    patch.install()
    assert ci._set_run_state is first_set_run_state
    assert ci._set_attempt is first_set_attempt
    assert ci._claim_ci_run is first_claim
    ci._set_run_state = original_set_run_state
    ci._set_attempt = original_set_attempt
    ci._claim_ci_run = original_claim
    patch._INSTALLED = False
