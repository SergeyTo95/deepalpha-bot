from __future__ import annotations

from typing import Any, Dict

from services.velia_admin_economy_v02_service import economy_v02_snapshot, render_economy_v02


def install_economy_v02_ui_patch(economy_routes_module: Any) -> None:
    """Attach v0.2 data to the existing threaded Economy snapshot.

    `admin_economy()` already calls `economy_snapshot` through
    `asyncio.to_thread`. Wrapping that callable keeps all v0.2 DB reads on the
    worker thread instead of performing synchronous psycopg2 work while aiohttp
    renders HTML on the event loop.
    """
    if getattr(economy_routes_module, "_velia_economy_v02_ui_installed", False):
        return

    original_snapshot = economy_routes_module.economy_snapshot
    original_body = economy_routes_module._economy_body

    def wrapped_snapshot() -> Dict[str, Any]:
        base = original_snapshot()
        if not isinstance(base, dict):
            base = {"available": False, "reason": "invalid_economy_snapshot"}
        else:
            base = dict(base)
        base["economy_v02"] = economy_v02_snapshot()
        return base

    def wrapped_body(admin: Any, data: Dict[str, Any]) -> str:
        v02 = data.get("economy_v02") if isinstance(data, dict) else None
        if not isinstance(v02, dict):
            v02 = {"available": False, "reason": "v02_snapshot_missing"}
        return render_economy_v02(admin, v02) + original_body(admin, data)

    economy_routes_module.economy_snapshot = wrapped_snapshot
    economy_routes_module._economy_body = wrapped_body
    economy_routes_module._velia_economy_v02_ui_installed = True
