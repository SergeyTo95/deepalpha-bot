from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from services.velia_admin_payments_service import payment_admin_snapshot


def _usd(value: Any) -> str:
    try:
        return f"${float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "Unavailable"


def _user_label(admin: Any, row: dict) -> str:
    username = str(row.get("username") or "").strip()
    first_name = str(row.get("first_name") or "").strip()
    if username:
        return f"@{admin._e(username)}"
    if first_name:
        return admin._e(first_name)
    return "—"


def _short(admin: Any, value: Any, size: int = 18) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    if len(text) <= size:
        return admin._e(text)
    half = max(4, size // 2)
    return admin._e(text[:half] + "…" + text[-half:])


def _payments_body(admin: Any, data: dict) -> str:
    if not data.get("available"):
        return (
            "<div class='card full'><h2>Payments foundation unavailable</h2>"
            f"<div class='muted'>{admin._e(data.get('reason') or 'unknown')}</div>"
            "<div class='hint'>Live money acceptance remains disabled.</div></div>"
        )

    summary = data.get("summary") or {}
    network_rows = "".join(
        "<tr>"
        f"<td><strong>{admin._e(str(row.get('network') or '').upper())}</strong></td>"
        f"<td>{admin._e(row.get('asset') or 'USDT')}</td>"
        f"<td>{'Enabled' if row.get('enabled') else 'Disabled'}</td>"
        f"<td>{admin._e(row.get('status') or '—')}</td>"
        f"<td>{admin._metric(row.get('chain_height'))}</td>"
        f"<td>{admin._metric(row.get('lag_blocks'))}</td>"
        f"<td>{admin._e(row.get('last_error_code') or '—')}</td>"
        "</tr>"
        for row in data.get("networks") or []
    ) or "<tr><td colspan='7' class='muted'>No worker state recorded.</td></tr>"

    channel_rows = "".join(
        "<tr>"
        f"<td>{admin._e(row.get('channel') or '—')}</td>"
        f"<td>{admin._metric(row.get('intents'))}</td>"
        f"<td>{_usd(row.get('confirmed_amount_usd'))}</td>"
        "</tr>"
        for row in data.get("channels") or []
    ) or "<tr><td colspan='3' class='muted'>No VELIA multi-rail payment intents yet.</td></tr>"

    intent_rows = "".join(
        "<tr>"
        f"<td>{admin._e(row.get('created_at') or '')}</td>"
        f"<td><a href='/admin/users/{int(row.get('user_id') or 0)}'><code>{int(row.get('user_id') or 0)}</code></a><div class='hint'>{_user_label(admin, row)}</div></td>"
        f"<td>{admin._e(row.get('product_code') or '—')}</td>"
        f"<td>{admin._e(row.get('channel') or '—')}</td>"
        f"<td>{admin._e(row.get('network') or '—')} / {admin._e(row.get('asset') or '—')}</td>"
        f"<td>{_usd(row.get('expected_amount_usd'))}</td>"
        f"<td>{_short(admin, row.get('deposit_address'))}</td>"
        f"<td>{admin._e(row.get('status') or '—')}</td>"
        "</tr>"
        for row in data.get("intents") or []
    ) or "<tr><td colspan='8' class='muted'>No VELIA multi-rail payment intents yet.</td></tr>"

    fulfillment_rows = "".join(
        f"<tr><td>{admin._e(row.get('status') or '—')}</td><td>{admin._metric(row.get('count'))}</td></tr>"
        for row in data.get("fulfillments") or []
    ) or "<tr><td colspan='2' class='muted'>No queued fulfillments.</td></tr>"

    legacy = data.get("legacy_ton") or {}
    legacy_text = (
        f"Legacy intents: {admin._metric(legacy.get('intents'))} · "
        f"fulfilled: {admin._metric(legacy.get('fulfilled'))} · "
        f"transactions: {admin._metric(legacy.get('transactions'))}"
        if legacy.get("available")
        else "Legacy TON tables unavailable."
    )

    return f"""
<div class='card full' style='border-color:rgba(246,200,95,.36);background:linear-gradient(145deg,rgba(54,42,14,.35),rgba(9,13,20,.96))'>
  <div class='label'>PAYMENT FOUNDATION</div>
  <div class='value' style='font-size:20px'>WATCH-ONLY · LIVE MONEY DISABLED</div>
  <div class='hint'>No blockchain polling, store receipt verification, wallet signing or automatic user credit is active in this stage.</div>
</div>

<div class='grid' style='margin-top:12px'>
  <div class='card'><div class='label'>Intents</div><div class='value'>{admin._metric(summary.get('total'))}</div><div class='hint'>Created 24h: {admin._metric(summary.get('created_24h'))}</div></div>
  <div class='card'><div class='label'>Awaiting</div><div class='value'>{admin._metric(summary.get('awaiting_payment'))}</div><div class='hint'>Detected {admin._metric(summary.get('detected'))} · Confirming {admin._metric(summary.get('confirming'))}</div></div>
  <div class='card'><div class='label'>Confirmed</div><div class='value'>{admin._metric(summary.get('confirmed'))}</div><div class='hint'>Fulfilled {admin._metric(summary.get('fulfilled'))}</div></div>
  <div class='card'><div class='label'>Failed</div><div class='value'>{admin._metric(summary.get('failed'))}</div><div class='hint'>Foundation only</div></div>
  <div class='card wide'><div class='label'>Confirmed amount · 30d</div><div class='value'>{_usd(summary.get('confirmed_amount_30d_usd'))}</div><div class='hint'>Persisted confirmed intent amounts only; not settlement accounting.</div></div>
</div>

<div class='card full' style='margin-top:12px'>
  <h2>Payment worker networks</h2>
  <div class='table-wrap'><table><thead><tr><th>Network</th><th>Asset</th><th>Flag</th><th>Status</th><th>Height</th><th>Lag</th><th>Last error</th></tr></thead><tbody>{network_rows}</tbody></table></div>
  <div class='hint'>TRON / Solana / TON are planned first. BNB / Polygon remain disabled until canonical USDT identifiers and finality rules are explicitly approved.</div>
</div>

<div class='grid' style='margin-top:12px'>
  <div class='card wide'><h2>Channels</h2><div class='table-wrap'><table><thead><tr><th>Channel</th><th>Intents</th><th>Confirmed amount</th></tr></thead><tbody>{channel_rows}</tbody></table></div></div>
  <div class='card wide'><h2>Fulfillment queue</h2><div class='table-wrap'><table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody>{fulfillment_rows}</tbody></table></div><div class='hint'>Foundation can only queue an idempotent fulfillment after confirmed status; it does not credit users.</div></div>
</div>

<div class='card full' style='margin-top:12px'><h2>Recent VELIA payment intents</h2><div class='table-wrap'><table><thead><tr><th>Created</th><th>User</th><th>Product</th><th>Channel</th><th>Rail</th><th>USD</th><th>Deposit</th><th>Status</th></tr></thead><tbody>{intent_rows}</tbody></table></div></div>

<div class='card full' style='margin-top:12px'>
  <h2>Legacy TON boundary</h2>
  <div>{legacy_text}</div>
  <div class='hint'>Existing TON payment_intents/transactions remain untouched and are not silently mixed into the new VELIA multi-rail accounting.</div>
</div>
<div class='hint' style='margin:10px 2px'>{admin._e(data.get('scope_note') or '')}</div>
"""


async def admin_payments(request: web.Request, admin: Any) -> web.Response:
    denied = await admin._guard(request)
    if denied:
        return denied
    data = await asyncio.to_thread(payment_admin_snapshot)
    body = _payments_body(admin, data)
    return web.Response(
        text=admin._layout("Payments", "Payments", admin._key(request), body, request.query.get("msg", "")),
        content_type="text/html",
    )


def setup_velia_admin_payments_routes(app: web.Application, admin_routes_module: Any) -> None:
    if app.get("velia_admin_payments_routes_installed"):
        return

    if not any(name == "Payments" for name, _path in admin_routes_module.SECTIONS):
        economy_index = next(
            (idx for idx, item in enumerate(admin_routes_module.SECTIONS) if item[0] == "Economy"),
            -1,
        )
        if economy_index >= 0:
            admin_routes_module.SECTIONS.insert(economy_index + 1, ("Payments", "/admin/economy/payments"))
        else:
            audit_index = next(
                (idx for idx, item in enumerate(admin_routes_module.SECTIONS) if item[0] == "Audit"),
                len(admin_routes_module.SECTIONS),
            )
            admin_routes_module.SECTIONS.insert(audit_index, ("Payments", "/admin/economy/payments"))

    async def handler(request: web.Request) -> web.Response:
        return await admin_payments(request, admin_routes_module)

    app.router.add_get("/admin/economy/payments", handler)
    app["velia_admin_payments_routes_installed"] = True
