from __future__ import annotations

from typing import Any, Dict

from services.velia_admin_economy_v02_service import economy_v02_snapshot, render_economy_v02


_VISIBLE_LABEL_REPLACEMENTS = (
    ("What is a VELIA token?", "What are VELIA Credits?"),
    ("VELIA Token", "VELIA Credits"),
    ("Token balances", "Credit balances"),
    ("Current token packages", "Current runtime Credit packages"),
    ("Draft feature token prices", "Legacy draft feature Credit prices"),
    ("Included VELIA tokens / month", "Included VELIA Credits / month"),
    ("Tokens/action", "Credits/action"),
    ("Token Ledger", "Credit Ledger"),
    ("Future commercial model", "Legacy Stage 2 draft workspace"),
    ("Draft plans", "Legacy draft plan cards"),
    ("Tokens are an accounting unit", "Credits are an accounting unit"),
    ("Tokens are separate from TON", "Credits are separate from TON"),
)


def _normalize_visible_credit_labels(html: str) -> str:
    result = str(html or "")
    for before, after in _VISIBLE_LABEL_REPLACEMENTS:
        result = result.replace(before, after)
    return result


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
        legacy_body = _normalize_visible_credit_labels(original_body(admin, data))
        compatibility_note = (
            "<div class='card full' style='margin-top:12px;border-color:rgba(246,200,95,.28)'>"
            "<div class='label'>Compatibility workspace</div>"
            "<div class='hint'>The editable Stage 2 cards below are legacy draft compatibility fields. "
            "Economy v0.2 above is the current commercial source of truth and remains read-only/not enforced. "
            "Editing legacy cards does not activate or rewrite v0.2.</div></div>"
        )
        return render_economy_v02(admin, v02) + compatibility_note + legacy_body

    economy_routes_module.economy_snapshot = wrapped_snapshot
    economy_routes_module._economy_body = wrapped_body
    economy_routes_module._velia_economy_v02_ui_installed = True
