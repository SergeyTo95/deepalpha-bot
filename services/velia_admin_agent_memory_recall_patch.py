from __future__ import annotations

from typing import Any

from services.velia_agent_memory_recall_service import (
    probe_atomic_search_support,
    recall_enabled,
)


def install(admin_routes_module: Any) -> None:
    current_queue_snapshot = getattr(admin_routes_module, "memory_queue_snapshot", None)
    if not callable(current_queue_snapshot):
        return

    queue_already_wrapped = bool(
        getattr(current_queue_snapshot, "_velia_agent_memory_recall_wrapper", False)
    )
    current_layout = getattr(admin_routes_module, "_layout", None)
    layout_already_wrapped = bool(
        callable(current_layout)
        and getattr(current_layout, "_velia_agent_memory_recall_wrapper", False)
    )

    if not queue_already_wrapped:
        original_queue_snapshot = current_queue_snapshot

        def memory_snapshot_with_recall() -> dict:
            value = original_queue_snapshot()
            snapshot = dict(value or {}) if isinstance(value, dict) else {"available": False}
            probe = probe_atomic_search_support()
            snapshot["agent_recall"] = {
                "runtime_enabled": recall_enabled(),
                "api_supported": bool(probe.get("supported")),
                "status": str(probe.get("status") or "unavailable"),
                "http_status": probe.get("http_status"),
                "latency_ms": probe.get("latency_ms"),
                "contract": str(probe.get("result_shape") or "") or None,
                "reason": str(probe.get("reason") or "")[:160] or None,
            }
            return snapshot

        memory_snapshot_with_recall._velia_agent_memory_recall_wrapper = True
        admin_routes_module.memory_queue_snapshot = memory_snapshot_with_recall

    # Snapshot diagnostics are useful even in minimal/test contexts that do not
    # expose the full Control Center renderer. Wrap presentation only when a
    # callable layout exists, and never double-wrap it.
    if callable(current_layout) and not layout_already_wrapped:
        original_layout = current_layout

        def layout_with_memory_label(
            title: str,
            active: str,
            key: str,
            body: str,
            flash: str = "",
        ) -> str:
            rendered_body = str(body or "")
            if str(title or "") == "Velyon Memory":
                rendered_body = rendered_body.replace(
                    "<h2>Shadow delivery queue</h2>",
                    "<h2>Memory operations</h2>",
                    1,
                )
                rendered_body = rendered_body.replace(
                    "<div class='card full'><h2>Storage / operations</h2>",
                    "<div class='card full'><h2>Recall safety</h2><div class='muted'>The Agent recall compatibility probe is read-only and uses a synthetic Velyon namespace. It does not create memory. Recall remains feature-gated until a controlled Agent acceptance test.</div></div><div class='card full'><h2>Storage / operations</h2>",
                    1,
                )
            return original_layout(title, active, key, rendered_body, flash)

        layout_with_memory_label._velia_agent_memory_recall_wrapper = True
        admin_routes_module._layout = layout_with_memory_label

    admin_routes_module._velia_admin_agent_memory_recall_installed = True
