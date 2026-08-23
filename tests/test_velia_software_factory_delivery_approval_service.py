from types import SimpleNamespace

import pytest

from services import velia_software_factory_delivery_approval_service as approval
from services.velia_software_factory_core_service import SoftwareFactoryError


def _candidate(*, fingerprint="fp-1", status="eligible", eligible=True):
    return {
        "candidate_id": "candidate-1",
        "source_type": "workspace_execution",
        "source_id": "execution-1",
        "source_fingerprint": fingerprint,
        "status": status,
        "release_eligible": eligible,
    }


def _install_gate(monkeypatch):
    monkeypatch.setattr(approval, "approval_enabled", lambda: True)
    monkeypatch.setattr(approval.delivery, "delivery_gate_enabled", lambda: True)
    monkeypatch.setattr(approval.rollout, "intake_allowed", lambda user_id: True)


def test_approval_defaults_fail_closed(monkeypatch):
    monkeypatch.delenv("VELIA_SOFTWARE_FACTORY_DELIVERY_APPROVAL_ENABLED", raising=False)
    status = approval.public_status()
    assert status["enabled"] is False
    assert status["mode"] == "record_only"
    assert status["append_only"] is True
    assert status["candidate_revalidation_required"] is True
    assert status["merge_supported"] is False
    assert status["deployment_supported"] is False


def test_approve_revalidates_exact_fingerprint(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate()
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    monkeypatch.setattr(
        approval.delivery,
        "evaluate_workspace_candidate",
        lambda module, user_id, execution_id, persist=False: {
            **candidate,
            "candidate_id": "",
        },
    )
    captured = {}

    def insert_event(module, user_id, value, decision, note):
        captured.update({"user_id": user_id, "candidate": value, "decision": decision, "note": note})
        return {"decision": decision, "approved": decision == "approved"}

    monkeypatch.setattr(approval, "_insert_event", insert_event)
    result = approval.record_decision(SimpleNamespace(), 7, "candidate-1", "approved", note="ship it")
    assert result["approved"] is True
    assert captured["decision"] == "approved"
    assert captured["candidate"]["source_fingerprint"] == "fp-1"


def test_approve_rejects_stale_candidate(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate(fingerprint="old")
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    monkeypatch.setattr(
        approval.delivery,
        "evaluate_workspace_candidate",
        lambda module, user_id, execution_id, persist=False: {
            **candidate,
            "source_fingerprint": "new",
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        approval.record_decision(SimpleNamespace(), 7, "candidate-1", "approved")
    assert exc.value.code == "velia_factory_delivery_candidate_stale"


def test_blocked_candidate_cannot_be_approved(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate(status="blocked", eligible=False)
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    with pytest.raises(SoftwareFactoryError) as exc:
        approval.record_decision(SimpleNamespace(), 7, "candidate-1", "approved")
    assert exc.value.code == "velia_factory_delivery_candidate_not_eligible"


def test_revoke_requires_active_approval(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate()
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    monkeypatch.setattr(
        approval,
        "latest_decision",
        lambda module, user_id, candidate_id: {"decision": "rejected", "approved": False},
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        approval.record_decision(SimpleNamespace(), 7, "candidate-1", "revoked")
    assert exc.value.code == "velia_factory_delivery_approval_not_active"


def test_require_current_approval_revalidates_again(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate()
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    monkeypatch.setattr(
        approval,
        "latest_decision",
        lambda module, user_id, candidate_id: {
            "decision": "approved",
            "approved": True,
            "source_fingerprint": "fp-1",
        },
    )
    monkeypatch.setattr(
        approval.delivery,
        "evaluate_workspace_candidate",
        lambda module, user_id, execution_id, persist=False: {
            **candidate,
            "status": "eligible",
            "release_eligible": True,
        },
    )
    result = approval.require_current_approval(SimpleNamespace(), 7, "candidate-1")
    assert result["current"] is True
    assert result["release_eligible"] is True
    assert result["merge_supported"] is False
    assert result["deployment_supported"] is False


def test_require_current_approval_fails_when_fresh_fingerprint_moves(monkeypatch):
    _install_gate(monkeypatch)
    candidate = _candidate(fingerprint="fp-1")
    monkeypatch.setattr(approval.delivery, "get_candidate", lambda module, user_id, candidate_id: candidate)
    monkeypatch.setattr(
        approval,
        "latest_decision",
        lambda module, user_id, candidate_id: {
            "decision": "approved",
            "approved": True,
            "source_fingerprint": "fp-1",
        },
    )
    monkeypatch.setattr(
        approval.delivery,
        "evaluate_workspace_candidate",
        lambda module, user_id, execution_id, persist=False: {
            **candidate,
            "source_fingerprint": "fp-2",
        },
    )
    with pytest.raises(SoftwareFactoryError) as exc:
        approval.require_current_approval(SimpleNamespace(), 7, "candidate-1")
    assert exc.value.code == "velia_factory_delivery_candidate_stale"
