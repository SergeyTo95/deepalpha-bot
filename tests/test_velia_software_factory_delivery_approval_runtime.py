from types import SimpleNamespace

from services import velia_software_factory_delivery_gate_runtime_patch as runtime


def test_runtime_registers_approval_boundary(monkeypatch):
    runtime._INSTALLED = False
    monkeypatch.setattr(runtime.delivery, "ensure_delivery_tables", lambda module: None)
    monkeypatch.setattr(runtime.approval, "ensure_approval_tables", lambda module: None)
    monkeypatch.setattr(
        runtime.delivery,
        "public_status",
        lambda: {"enabled": False, "mode": "read_only_candidate"},
    )
    monkeypatch.setattr(
        runtime.approval,
        "public_status",
        lambda: {"enabled": False, "mode": "record_only"},
    )
    monkeypatch.setattr(
        runtime.delivery,
        "evaluate_workspace_candidate",
        lambda module, user_id, execution_id, persist=True: {
            "user_id": user_id,
            "execution_id": execution_id,
            "persist": persist,
        },
    )
    monkeypatch.setattr(
        runtime.delivery,
        "get_candidate",
        lambda module, user_id, candidate_id: {"candidate_id": candidate_id},
    )
    monkeypatch.setattr(runtime.delivery, "list_candidates", lambda module, user_id, execution_id, limit: [])
    monkeypatch.setattr(
        runtime.approval,
        "latest_decision",
        lambda module, user_id, candidate_id: {"candidate_id": candidate_id, "state": "none"},
    )
    monkeypatch.setattr(
        runtime.approval,
        "record_decision",
        lambda module, user_id, candidate_id, decision, note="": {
            "candidate_id": candidate_id,
            "decision": decision,
            "note": note,
        },
    )
    monkeypatch.setattr(
        runtime.approval,
        "require_current_approval",
        lambda module, user_id, candidate_id: {"candidate_id": candidate_id, "current": True},
    )

    module = SimpleNamespace()
    runtime.install(module)

    assert module.delivery_gate_status()["mode"] == "read_only_candidate"
    assert module.delivery_approval_status()["mode"] == "record_only"
    assert module.get_delivery_approval(7, "candidate-1")["state"] == "none"
    decision = module.record_delivery_decision(7, "candidate-1", "approved", "ship")
    assert decision["decision"] == "approved"
    assert decision["note"] == "ship"
    assert module.require_current_delivery_approval(7, "candidate-1")["current"] is True
