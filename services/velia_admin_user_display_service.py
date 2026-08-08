import re
from typing import Any

from aiohttp import web


_EMPTY_USERNAME_WITH_NAME = re.compile(r"<td>@—</td><td>(.*?)</td>")


def apply_admin_users_display_fallback(request: Any, response: web.StreamResponse) -> web.StreamResponse:
    """Make the mobile Users table useful when a Telegram username is absent.

    The canonical data remains unchanged. The existing admin route already
    HTML-escapes `first_name`; this presentation-only pass reuses that escaped
    value as the visible User label when the username column would otherwise
    contain `@—`.
    """
    if str(getattr(request, "method", "") or "").upper() not in {"GET", "HEAD"}:
        return response
    if str(getattr(request, "path", "") or "") != "/admin/users":
        return response
    if not isinstance(response, web.Response) or int(response.status or 0) != 200:
        return response
    if str(response.content_type or "").lower() != "text/html":
        return response

    text = response.text
    if not text:
        return response

    text = text.replace("<th>Username</th><th>Name</th>", "<th>User</th><th>Name</th>")
    text = _EMPTY_USERNAME_WITH_NAME.sub(
        lambda match: f"<td>{match.group(1)}</td><td>{match.group(1)}</td>",
        text,
    )
    response.text = text
    return response
