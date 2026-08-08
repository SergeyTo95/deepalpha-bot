from __future__ import annotations

from typing import Any, Dict

from services.velia_admin_economy_v02_branding_service import (
    DEEP_NAME,
    NEURAL_CORE_NAME,
    normalize_public_html,
)
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

_PUBLIC_SKU_NAME_OVERRIDES = {
    "velia_core": NEURAL_CORE_NAME,
    "velia_deep": DEEP_NAME,
}


def _normalize_visible_credit_labels(html: str) -> str:
    result = str(html or "")
    for before, after in _VISIBLE_LABEL_REPLACEMENTS:
        result = result.replace(before, after)
    return normalize_public_html(result)


def _normalize_v02_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(data or {})

    normalized_plans = []
    for item in result.get("plans") or []:
        row = dict(item or {})
        row["core_policy"] = normalize_public_html(str(row.get("core_policy") or ""))
        row["notes"] = normalize_public_html(str(row.get("notes") or ""))
        normalized_plans.append(row)
    result["plans"] = normalized_plans

    normalized_skus = []
    for item in result.get("skus") or []:
        row = dict(item or {})
        code = str(row.get("code") or "")
        override = _PUBLIC_SKU_NAME_OVERRIDES.get(code)
        if override:
            row["name"] = override
        row["pricing_formula"] = normalize_public_html(str(row.get("pricing_formula") or ""))
        row["notes"] = normalize_public_html(str(row.get("notes") or ""))
        normalized_skus.append(row)
    result["skus"] = normalized_skus

    normalized_policies = []
    for item in result.get("policies") or []:
        row = dict(item or {})
        row["description"] = normalize_public_html(str(row.get("description") or ""))
        normalized_policies.append(row)
    result["policies"] = normalized_policies
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
        base["economy_v02"] = _normalize_v02_snapshot(economy_v02_snapshot())
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
        current_body = normalize_public_html(render_economy_v02(admin, v02))
        return current_body + compatibility_note + legacy_body

    economy_routes_module.economy_snapshot = wrapped_snapshot
    economy_routes_module._economy_body = wrapped_body
    economy_routes_module._velia_economy_v02_ui_installed = True
