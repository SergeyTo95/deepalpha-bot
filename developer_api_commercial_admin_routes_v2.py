"""Commercial Admin route compatibility for query-key authenticated Admin Center sessions."""

import re
from html import escape
from urllib.parse import quote_plus

from aiohttp import web

# Install final service/health patches before the base Admin module imports service functions.
from services import developer_api_commercial_final_service as _commercial_final  # noqa: F401
import developer_api_commercial_admin_routes as base


_ORIGINAL_DASHBOARD = base._commercial_dashboard


def _request_key(request: web.Request) -> str:
    return str(request.query.get("key") or "")


def _inject_admin_key(html: str, key: str) -> str:
    if not key:
        return html
    encoded = escape(quote_plus(key), quote=True)
    hidden = f"<input type='hidden' name='key' value='{escape(key, quote=True)}'>"

    def replace(match: re.Match) -> str:
        action = match.group(1)
        separator = "&" if "?" in action else "?"
        return (
            f"<form method='post' action='{action}{separator}key={encoded}'"
            f"{match.group(2)}>{hidden}"
        )

    return re.sub(
        r"<form method='post' action='(/admin/api/[^']+)'([^>]*)>",
        replace,
        html,
    )


def _authenticated_dashboard(request: web.Request) -> str:
    return _inject_admin_key(_ORIGINAL_DASHBOARD(request), _request_key(request))


async def _preserve_redirect(handler, request: web.Request) -> web.StreamResponse:
    key = _request_key(request)
    if not key and request.can_read_body:
        form = await request.post()
        key = str(form.get("key") or "")
    response = await handler(request)
    if not key or not isinstance(response, web.HTTPFound):
        return response
    location = str(response.headers.get("Location") or "/admin/api")
    separator = "&" if "?" in location else "?"
    return web.HTTPFound(f"{location}{separator}key={quote_plus(key)}")


def _wrap(handler):
    async def wrapped(request: web.Request):
        return await _preserve_redirect(handler, request)
    return wrapped


base._commercial_dashboard = _authenticated_dashboard
base.admin_upsert_package = _wrap(base.admin_upsert_package)
base.admin_approve_live = _wrap(base.admin_approve_live)
base.admin_reject_live = _wrap(base.admin_reject_live)
base.admin_suspend_live = _wrap(base.admin_suspend_live)
base.admin_mark_paid = _wrap(base.admin_mark_paid)
base.admin_credit_invoice = _wrap(base.admin_credit_invoice)
base.admin_cancel_invoice = _wrap(base.admin_cancel_invoice)
base.admin_scan_payments = _wrap(base.admin_scan_payments)

install = base.install
setup_developer_api_commercial_admin_routes = base.setup_developer_api_commercial_admin_routes
