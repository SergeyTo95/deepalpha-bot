from types import SimpleNamespace

from services import velia_admin_agent_memory_recall_patch as admin_patch
from services import velia_admin_observability_service as observability


def test_recall_snapshot_augmentation_is_safe_and_read_only(monkeypatch):
    monkeypatch.setattr(admin_patch, "recall_enabled", lambda: False)
    monkeypatch.setattr(
        admin_patch,
        "probe_atomic_search_support",
        lambda: {
            "status": "online",
            "supported": True,
            "http_status": 200,
            "latency_ms": 12,
            "result_shape": "v3_atomic_search",
        },
    )

    original = {"available": True, "pending": 2}
    snapshot = admin_patch.augment_memory_snapshot(original)

    assert original == {"available": True, "pending": 2}
    assert snapshot["available"] is True
    assert snapshot["pending"] == 2
    assert snapshot["agent_recall"] == {
        "runtime_enabled": False,
        "api_supported": True,
        "status": "online",
        "http_status": 200,
        "latency_ms": 12,
        "contract": "v3_atomic_search",
        "reason": None,
    }


def test_admin_memory_ui_patch_supports_module_without_layout():
    sentinel = lambda: {"available": True, "pending": 1}
    module = SimpleNamespace(memory_queue_snapshot=sentinel)

    admin_patch.install(module)

    assert module.memory_queue_snapshot is sentinel
    assert not hasattr(module, "_layout")
    assert getattr(module, "_velia_admin_agent_memory_recall_installed", False) is True


def test_admin_memory_ui_patch_relabels_memory_card_and_is_idempotent():
    sentinel = lambda: {"available": True}
    module = SimpleNamespace(
        memory_queue_snapshot=sentinel,
        _layout=lambda title, active, key, body, flash="": body,
    )

    admin_patch.install(module)
    first_layout = module._layout
    admin_patch.install(module)
    assert module.memory_queue_snapshot is sentinel
    assert module._layout is first_layout

    body = (
        "<div class='card wide'><h2>Shadow delivery queue</h2><pre>{}</pre></div>"
        "<div class='card full'><h2>Storage / operations</h2><div>Unavailable</div></div>"
    )
    rendered = module._layout("Velyon Memory", "Memory", "csrf", body)
    assert "<h2>Memory operations</h2>" in rendered
    assert "<h2>Recall safety</h2>" in rendered
    assert "read-only" in rendered
    assert "does not create memory" in rendered


def test_observability_rebinding_preserves_function_identity_and_adds_recall(monkeypatch):
    module = SimpleNamespace(
        overview_snapshot=lambda: {},
        ai_snapshot=lambda: {},
        recent_errors=lambda limit=50: [],
        memory_queue_snapshot=object(),
        velyon_memory_health=lambda: {"status": "unknown"},
        _layout=lambda title, active, key, body, flash="": body,
    )
    monkeypatch.setattr(
        observability.control,
        "memory_queue_snapshot",
        lambda: {"available": True, "pending": 4, "source": "observability"},
    )
    monkeypatch.setattr(admin_patch, "recall_enabled", lambda: False)
    monkeypatch.setattr(
        admin_patch,
        "probe_atomic_search_support",
        lambda: {
            "status": "online",
            "supported": True,
            "http_status": 200,
            "latency_ms": 9,
            "result_shape": "v3_atomic_search",
        },
    )

    observability.install(module)
    snapshot = module.memory_queue_snapshot()

    assert module.memory_queue_snapshot is observability.memory_queue_snapshot
    assert snapshot["available"] is True
    assert snapshot["source"] == "observability"
    assert snapshot["pending"] == 4
    assert snapshot["agent_recall"]["api_supported"] is True
    assert snapshot["agent_recall"]["contract"] == "v3_atomic_search"
    assert getattr(module, "_velia_admin_observability_installed", False) is True
    assert getattr(module, "_velia_admin_agent_memory_recall_installed", False) is True


def test_recall_diagnostics_live_in_observability_not_early_bootstrap():
    bootstrap = open("services/velia_admin_economy_bootstrap_service.py", encoding="utf-8").read()
    observability_source = open("services/velia_admin_observability_service.py", encoding="utf-8").read()
    assert "install_agent_memory_recall_admin" not in bootstrap
    assert "augment_memory_snapshot(value)" in observability_source
    rebind_index = observability_source.index("admin_routes_module.memory_queue_snapshot = memory_queue_snapshot")
    install_index = observability_source.rindex("install_agent_memory_recall_admin(admin_routes_module)")
    assert rebind_index < install_index


def test_admin_recall_diagnostic_never_exposes_secret_or_memory_content_fields():
    source = open("services/velia_admin_agent_memory_recall_patch.py", encoding="utf-8").read()
    for forbidden in (
        "Authorization",
        "VELIA_MEMORY_API_KEY",
        "private_key",
        "seed",
        "mnemonic",
        '"items"',
        '"content"',
        '"query"',
    ):
        assert forbidden not in source
    assert "probe_atomic_search_support" in source
    assert "runtime_enabled" in source
    assert "api_supported" in source
