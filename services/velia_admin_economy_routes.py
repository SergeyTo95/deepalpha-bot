from __future__ import annotations

import asyncio
from typing import Any, Optional
from urllib.parse import quote_plus

from aiohttp import web

from services.velia_admin_economy_service import (
    economy_snapshot,
    update_draft_feature,
    update_draft_plan,
)


def _optional_float(value: Any) -> Optional[float]:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    return float(raw)


def _optional_int(value: Any) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    return int(raw)


def _usd(value: Any) -> str:
    try:
        return f"${float(value or 0):.4f}"
    except (TypeError, ValueError):
        return "Unavailable"


def _user_label(admin: Any, item: dict) -> str:
    username = str(item.get("username") or "").strip()
    first_name = str(item.get("first_name") or "").strip()
    if username:
        return f"@{admin._e(username)}"
    if first_name:
        return admin._e(first_name)
    return "—"


def _economy_body(admin: Any, data: dict) -> str:
    if not data.get("available"):
        return (
            "<div class='card full'><h2>Economy telemetry unavailable</h2>"
            f"<div class='muted'>{admin._e(data.get('reason') or 'unknown')}</div></div>"
        )

    costs = data.get("costs") or {}
    ai = costs.get("ai") or {}
    images = costs.get("images") or {}
    videos = costs.get("videos") or {}
    users = data.get("users") or {}
    token = data.get("token_definition") or {}
    runtime = data.get("runtime_pricing") or {}

    provider_rows = "".join(
        "<tr>"
        f"<td>{admin._e(row.get('provider') or '—')}</td>"
        f"<td><code>{admin._e(row.get('model') or '—')}</code></td>"
        f"<td>{admin._metric(row.get('requests'))}</td>"
        f"<td>{admin._metric(row.get('success_rate'), suffix='%')}</td>"
        f"<td>{admin._metric(row.get('avg_latency_ms'), suffix=' ms')}</td>"
        f"<td>{admin._metric(row.get('input_tokens'))}</td>"
        f"<td>{admin._metric(row.get('output_tokens'))}</td>"
        f"<td>{_usd(row.get('estimated_cost_usd'))}</td>"
        "</tr>"
        for row in data.get("providers") or []
    ) or "<tr><td colspan='8' class='muted'>No persisted provider usage in the last 30 days.</td></tr>"

    top_rows = "".join(
        "<tr>"
        f"<td><a href='/admin/users/{int(row.get('user_id') or 0)}'><code>{int(row.get('user_id') or 0)}</code></a></td>"
        f"<td>{_user_label(admin, row)}</td>"
        f"<td>{admin._metric(row.get('requests'))}</td>"
        f"<td>{admin._metric(row.get('input_tokens'))}</td>"
        f"<td>{admin._metric(row.get('output_tokens'))}</td>"
        f"<td>{_usd(row.get('estimated_cost_usd'))}</td>"
        "</tr>"
        for row in data.get("top_cost_users") or []
    ) or "<tr><td colspan='6' class='muted'>No persisted user AI cost data in the last 30 days.</td></tr>"

    package_rows = "".join(
        "<tr>"
        f"<td>{int(row.get('id') or 0)}</td>"
        f"<td>{admin._e(row.get('name') or '—')}</td>"
        f"<td>{admin._metric(row.get('tokens'))}</td>"
        f"<td>{admin._metric(row.get('price_ton'), suffix=' TON')}</td>"
        f"<td>{admin._metric(row.get('discount_percent'), suffix='%')}</td>"
        f"<td>{'Active' if row.get('is_active') else 'Disabled'}</td>"
        "</tr>"
        for row in data.get("token_packages") or []
    ) or "<tr><td colspan='6' class='muted'>No token packages configured.</td></tr>"

    plan_cards = "".join(
        f"""
        <div class='card wide'>
          <div class='label'>DRAFT PLAN · {admin._e(plan.get('code') or '')}</div>
          <h2 style='margin-top:7px'>{admin._e(plan.get('name') or '')}</h2>
          <form method='post' action='/admin/economy/draft/plan/{admin._e(plan.get('code') or '')}'>
            <div class='action-row'>
              <label>Monthly price · USD
                <input type='number' min='0' max='1000000' step='0.01' name='monthly_price_usd' value='{'' if plan.get('monthly_price_usd') is None else admin._e(plan.get('monthly_price_usd'))}' placeholder='TBD'>
              </label>
              <label>Included VELIA tokens / month
                <input type='number' min='0' max='1000000000' step='1' name='monthly_tokens' value='{'' if plan.get('monthly_tokens') is None else int(plan.get('monthly_tokens') or 0)}' placeholder='TBD'>
              </label>
            </div>
            <label style='margin-top:9px'>Draft notes
              <input name='notes' maxlength='1000' value='{admin._e(plan.get('notes') or '')}'>
            </label>
            <div class='hint'>Not connected to checkout, subscriptions, limits or token debits.</div>
            <button class='primary' style='margin-top:10px' type='submit' data-confirm='Save this commercial draft only? It will not change live billing.'>Save draft</button>
          </form>
        </div>
        """
        for plan in data.get("draft_plans") or []
    )

    feature_rows = "".join(
        f"""
        <tr>
          <td>{admin._e(item.get('name') or '')}<div class='hint'>{admin._e(item.get('unit_label') or '')}</div></td>
          <td>
            <form class='inline' method='post' action='/admin/economy/draft/feature/{admin._e(item.get('code') or '')}'>
              <label>Tokens/action
                <input style='width:120px' type='number' min='0' max='1000000000' step='1' name='tokens_per_action' value='{'' if item.get('tokens_per_action') is None else int(item.get('tokens_per_action') or 0)}' placeholder='TBD'>
              </label>
              <label>Notes
                <input style='min-width:240px' name='notes' maxlength='1000' value='{admin._e(item.get('notes') or '')}'>
              </label>
              <button type='submit' data-confirm='Save this draft feature price? It is not enforced in production.'>Save</button>
            </form>
          </td>
        </tr>
        """
        for item in data.get("draft_features") or []
    ) or "<tr><td colspan='2' class='muted'>Draft feature matrix unavailable.</td></tr>"

    ledger = data.get("ledger") or {}
    ledger_rows = "".join(
        "<tr>"
        f"<td>{admin._e(row.get('created_at') or '')}</td>"
        f"<td><a href='/admin/users/{int(row.get('user_id') or 0)}'><code>{int(row.get('user_id') or 0)}</code></a><div class='hint'>{_user_label(admin, row)}</div></td>"
        f"<td class='{'good-text' if int(row.get('delta_tokens') or 0) >= 0 else 'bad-text'}'>{int(row.get('delta_tokens') or 0):+d}</td>"
        f"<td>{admin._metric(row.get('balance_before'))} → {admin._metric(row.get('balance_after'))}</td>"
        f"<td>{admin._e(row.get('source') or 'balance_change')}</td>"
        "</tr>"
        for row in ledger.get("items") or []
    ) or "<tr><td colspan='5' class='muted'>No token balance changes recorded since Stage 2 tracking started.</td></tr>"

    runtime_rows = "".join(
        f"<tr><td><code>{admin._e(key)}</code></td><td>{admin._e(value)}</td></tr>"
        for key, value in sorted(runtime.items())
    )

    return f"""
<div class='card full' style='border-color:rgba(246,200,95,.36);background:linear-gradient(145deg,rgba(54,42,14,.35),rgba(9,13,20,.96))'>
  <div class='label'>Commercial model status</div>
  <div class='value' style='font-size:20px'>DRAFT ONLY · NOT ENFORCED</div>
  <div class='hint'>Current production billing is shown separately below. Free / Plus / Pro and feature token prices are a planning workspace until we explicitly promote an economy version to runtime.</div>
</div>

<div class='grid' style='margin-top:12px'>
  <div class='card'><div class='label'>Persisted cost · 24h</div><div class='value'>{_usd(costs.get('persisted_estimated_total_24h_usd'))}</div><div class='hint'>AI + images + videos</div></div>
  <div class='card'><div class='label'>Persisted cost · 7d</div><div class='value'>{_usd(costs.get('persisted_estimated_total_7d_usd'))}</div><div class='hint'>Known provider estimates only</div></div>
  <div class='card'><div class='label'>Persisted cost · 30d</div><div class='value'>{_usd(costs.get('persisted_estimated_total_30d_usd'))}</div><div class='hint'>No guessed pricing</div></div>
  <div class='card'><div class='label'>AI requests · 30d</div><div class='value'>{admin._metric(ai.get('requests_30d') if ai.get('available') else None)}</div><div class='hint'>Input {admin._metric(ai.get('input_tokens_30d') if ai.get('available') else None)} · Output {admin._metric(ai.get('output_tokens_30d') if ai.get('available') else None)}</div></div>
  <div class='card'><div class='label'>Images · 30d</div><div class='value'>{admin._metric(images.get('count_30d') if images.get('available') else None)}</div><div class='hint'>Cost {_usd(images.get('cost_30d_usd')) if images.get('available') else 'Unavailable'}</div></div>
  <div class='card'><div class='label'>Videos · 30d</div><div class='value'>{admin._metric(videos.get('count_30d') if videos.get('available') else None)}</div><div class='hint'>Cost {_usd(videos.get('cost_30d_usd')) if videos.get('available') else 'Unavailable'}</div></div>
</div>
<div class='hint' style='margin:7px 2px 18px'>{admin._e(costs.get('scope_note') or '')}</div>

<div class='card full'><h2>Provider / model cost · 30 days</h2><div class='table-wrap'><table><thead><tr><th>Provider</th><th>Model</th><th>Requests</th><th>Success</th><th>Latency</th><th>Input</th><th>Output</th><th>Est. cost</th></tr></thead><tbody>{provider_rows}</tbody></table></div></div>

<div class='grid' style='margin-top:12px'>
  <div class='card'><div class='label'>Users</div><div class='value'>{admin._metric(users.get('total_users'))}</div><div class='hint'>VIP {admin._metric(users.get('vip_users'))}</div></div>
  <div class='card'><div class='label'>New today</div><div class='value'>{admin._metric(users.get('new_today'))}</div><div class='hint'>7d {admin._metric(users.get('new_7d'))} · 30d {admin._metric(users.get('new_30d'))}</div></div>
  <div class='card'><div class='label'>VELIA Chat DAU</div><div class='value'>{admin._metric(users.get('chat_dau') if users.get('chat_activity_available') else None)}</div><div class='hint'>WAU {admin._metric(users.get('chat_wau') if users.get('chat_activity_available') else None)} · MAU {admin._metric(users.get('chat_mau') if users.get('chat_activity_available') else None)}</div></div>
  <div class='card'><div class='label'>Token balances</div><div class='value'>{admin._metric(users.get('token_balance_total'))}</div><div class='hint'>{admin._metric(users.get('users_with_tokens'))} users with positive balance</div></div>
</div>
<div class='hint' style='margin:7px 2px 18px'>DAU / WAU / MAU here are explicitly based on persisted VELIA chat messages, not a fabricated whole-platform active-user metric.</div>

<div class='card full'><h2>Highest persisted AI cost users · 30 days</h2><div class='table-wrap'><table><thead><tr><th>Telegram ID</th><th>User</th><th>Requests</th><th>Input</th><th>Output</th><th>Est. cost</th></tr></thead><tbody>{top_rows}</tbody></table></div></div>

<div class='grid' style='margin-top:12px'>
  <div class='card wide'>
    <div class='label'>What is a VELIA token?</div>
    <h2 style='margin-top:7px'>{admin._e(token.get('name') or 'VELIA Token')}</h2>
    <div>{admin._e(token.get('description') or '')}</div>
    <div class='hint'>Economics status: {admin._e(token.get('economics_status') or 'draft')} · fixed USD value: TBD</div>
  </div>
  <div class='card wide'>
    <div class='label'>Boundary</div>
    <h2 style='margin-top:7px'>Internal usage credit</h2>
    <div>Tokens are an accounting unit for premium actions. They are separate from TON, wallet jettons and provider/API tokens.</div>
    <div class='hint'>No blockchain promise, cash redemption or fixed exchange rate is implied by this admin draft.</div>
  </div>
</div>

<div class='card full' style='margin-top:12px'><h2>Current production pricing · enforced today</h2><div class='table-wrap'><table><thead><tr><th>Runtime setting</th><th>Current value</th></tr></thead><tbody>{runtime_rows}</tbody></table></div><div class='hint'>Read-only here. Stage 2 does not silently change existing customer billing.</div></div>

<div class='card full' style='margin-top:12px'><h2>Current token packages</h2><div class='table-wrap'><table><thead><tr><th>ID</th><th>Name</th><th>Tokens</th><th>Price</th><th>Discount</th><th>Status</th></tr></thead><tbody>{package_rows}</tbody></table></div><div class='hint'>These are the existing runtime token packages. Draft plans below are independent.</div></div>

<div style='margin-top:20px'><div class='label'>Future commercial model</div><h2 style='font-size:20px;margin-top:5px'>Draft plans</h2></div>
<div class='grid'>{plan_cards}</div>

<div class='card full' style='margin-top:12px'><h2>Draft feature token prices</h2><div class='table-wrap'><table><thead><tr><th>Feature</th><th>Draft pricing</th></tr></thead><tbody>{feature_rows}</tbody></table></div><div class='hint'>Saving these fields changes only the planning tables and writes an admin Audit entry. It does not affect runtime debits.</div></div>

<div class='card full' style='margin-top:12px'><h2>Token Ledger</h2><div class='hint' style='margin-bottom:10px'>Automatic balance-change tracking starts with Stage 2 deployment. The database trigger is fail-open: ledger failure cannot block a balance update.</div><div class='table-wrap'><table><thead><tr><th>Time</th><th>User</th><th>Delta</th><th>Balance</th><th>Source</th></tr></thead><tbody>{ledger_rows}</tbody></table></div><div class='hint'>Events recorded: {admin._metric(ledger.get('total_events') if ledger.get('available') else None)} · tracking started: {admin._e(ledger.get('tracking_started_at') or 'after first balance change')}</div></div>
"""


async def admin_economy(request: web.Request, admin: Any) -> web.Response:
    denied = await admin._guard(request)
    if denied:
        return denied
    data = await asyncio.to_thread(economy_snapshot)
    body = _economy_body(admin, data)
    return web.Response(
        text=admin._layout("Economy", "Economy", admin._key(request), body, request.query.get("msg", "")),
        content_type="text/html",
    )


async def update_plan(request: web.Request, admin: Any) -> web.Response:
    denied = await admin._guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        price = _optional_float(form.get("monthly_price_usd"))
        tokens = _optional_int(form.get("monthly_tokens"))
    except ValueError:
        raise web.HTTPFound("/admin/economy?msg=" + quote_plus("Invalid draft plan number"))
    session = request.get("velia_admin_session") or {}
    result = await asyncio.to_thread(
        update_draft_plan,
        admin_user_id=int(session.get("admin_user_id") or 0),
        code=str(request.match_info.get("code") or ""),
        monthly_price_usd=price,
        monthly_tokens=tokens,
        notes=str(form.get("notes") or ""),
        request_id=str(request.get("velia_request_id") or ""),
        source="web",
        ip=request.remote or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    msg = "Draft plan saved" if result.get("ok") else f"Draft plan update failed: {result.get('error') or 'unknown'}"
    raise web.HTTPFound("/admin/economy?msg=" + quote_plus(msg))


async def update_feature(request: web.Request, admin: Any) -> web.Response:
    denied = await admin._guard(request)
    if denied:
        return denied
    form = await request.post()
    try:
        tokens = _optional_int(form.get("tokens_per_action"))
    except ValueError:
        raise web.HTTPFound("/admin/economy?msg=" + quote_plus("Invalid draft token amount"))
    session = request.get("velia_admin_session") or {}
    result = await asyncio.to_thread(
        update_draft_feature,
        admin_user_id=int(session.get("admin_user_id") or 0),
        code=str(request.match_info.get("code") or ""),
        tokens_per_action=tokens,
        notes=str(form.get("notes") or ""),
        request_id=str(request.get("velia_request_id") or ""),
        source="web",
        ip=request.remote or "",
        user_agent=request.headers.get("User-Agent", ""),
    )
    msg = "Draft feature price saved" if result.get("ok") else f"Draft feature update failed: {result.get('error') or 'unknown'}"
    raise web.HTTPFound("/admin/economy?msg=" + quote_plus(msg))


def setup_velia_admin_economy_routes(app: web.Application, admin_routes_module: Any) -> None:
    if app.get("velia_admin_economy_routes_installed"):
        return

    if not any(name == "Economy" for name, _path in admin_routes_module.SECTIONS):
        audit_index = next(
            (idx for idx, item in enumerate(admin_routes_module.SECTIONS) if item[0] == "Audit"),
            len(admin_routes_module.SECTIONS),
        )
        admin_routes_module.SECTIONS.insert(audit_index, ("Economy", "/admin/economy"))

    async def economy_handler(request: web.Request) -> web.Response:
        return await admin_economy(request, admin_routes_module)

    async def plan_handler(request: web.Request) -> web.Response:
        return await update_plan(request, admin_routes_module)

    async def feature_handler(request: web.Request) -> web.Response:
        return await update_feature(request, admin_routes_module)

    app.router.add_get("/admin/economy", economy_handler)
    app.router.add_post("/admin/economy/draft/plan/{code}", plan_handler)
    app.router.add_post("/admin/economy/draft/feature/{code}", feature_handler)
    app["velia_admin_economy_routes_installed"] = True
