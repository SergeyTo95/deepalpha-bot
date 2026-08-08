from __future__ import annotations

from typing import Any, Dict

from services.velia_admin_economy_v01_service import economy_v01_snapshot, render_economy_v01


def install_economy_v01_ui_patch(economy_routes_module: Any) -> None:
    """Attach v0.1 data to the existing threaded Economy snapshot.

    `admin_economy()` already calls `economy_snapshot` through
    `asyncio.to_thread`. Wrapping that callable keeps all v0.1 DB reads on the
    worker thread instead of performing synchronous psycopg2 work while aiohttp
    is rendering HTML on the event loop.
    """
    if getattr(economy_routes_module, "_velia_economy_v01_ui_installed", False):
        return

    original_snapshot = economy_routes_module.economy_snapshot
    original_body = economy_routes_module._economy_body

    def wrapped_snapshot() -> Dict[str, Any]:
        base = original_snapshot()
        if not isinstance(base, dict):
            base = {"available": False, "reason": "invalid_economy_snapshot"}
        else:
            base = dict(base)
        base["economy_v01"] = economy_v01_snapshot()
        return base

    def wrapped_body(admin: Any, data: Dict[str, Any]) -> str:
        v01 = data.get("economy_v01") if isinstance(data, dict) else None
        if not isinstance(v01, dict):
            v01 = {"available": False, "reason": "v01_snapshot_missing"}
        return render_economy_v01(admin, v01) + original_body(admin, data)

    economy_routes_module.economy_snapshot = wrapped_snapshot
    economy_routes_module._economy_body = wrapped_body
    economy_routes_module._velia_economy_v01_ui_installed = True
