"""Durable browser auth-state helpers for VELIA Agent Core."""
import asyncio
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, WSMsgType

SNAPSHOT_NAME = "session-cookies.json"
COOKIE_PARAM_FIELDS = (
    "name", "value", "domain", "path", "secure", "httpOnly",
    "sameSite", "priority", "sourceScheme", "sourcePort", "partitionKey",
)


def _snapshot_path(root):
    return Path(root) / SNAPSHOT_NAME


def _session_cookie_params(cookies):
    rows = []
    for cookie in cookies:
        if not isinstance(cookie, dict) or cookie.get("session") is not True:
            continue
        if not isinstance(cookie.get("name"), str) or not isinstance(cookie.get("value"), str):
            continue
        row = {}
        for key in COOKIE_PARAM_FIELDS:
            value = cookie.get(key)
            if value is not None:
                row[key] = value
        # Session cookies intentionally omit "expires" when restored.
        rows.append(row)
    return rows


async def _browser_ws_url(endpoint):
    timeout = ClientTimeout(total=5)
    async with ClientSession(timeout=timeout) as client:
        async with client.get(endpoint.rstrip("/") + "/json/version") as response:
            if response.status != 200:
                raise RuntimeError("browser_cdp_version_unavailable")
            payload = await response.json()
    url = payload.get("webSocketDebuggerUrl")
    if not isinstance(url, str):
        raise RuntimeError("browser_cdp_websocket_missing")
    parsed = urlsplit(url)
    if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("browser_cdp_websocket_not_loopback")
    return url


async def _cdp_call(ws, request_id, method, params=None, session_id=None):
    request = {"id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    if session_id is not None:
        request["sessionId"] = session_id
    await ws.send_json(request)
    while True:
        message = await ws.receive(timeout=5)
        if message.type == WSMsgType.TEXT:
            payload = json.loads(message.data)
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                detail = payload["error"].get("message") if isinstance(payload["error"], dict) else payload["error"]
                raise RuntimeError(f"browser_cdp_error:{str(detail)[:160]}")
            return payload.get("result") or {}
        if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
            raise RuntimeError("browser_cdp_websocket_closed")


async def _open_browser_ws(endpoint):
    url = await _browser_ws_url(endpoint)
    client = ClientSession(timeout=ClientTimeout(total=8))
    try:
        ws = await client.ws_connect(url, max_msg_size=2 * 1024 * 1024)
    except Exception:
        await client.close()
        raise
    return client, ws


async def save_session_cookies(endpoint, root):
    client, ws = await _open_browser_ws(endpoint)
    try:
        result = await _cdp_call(ws, 1, "Storage.getCookies")
    finally:
        await ws.close()
        await client.close()
    cookies = _session_cookie_params(result.get("cookies") or [])
    target = _snapshot_path(root)
    temporary = Path(root) / f".{SNAPSHOT_NAME}.{secrets.token_hex(4)}.tmp"
    payload = json.dumps({"version": 1, "cookies": cookies}, separators=(",", ":"))
    await asyncio.to_thread(temporary.write_text, payload, encoding="utf-8")
    await asyncio.to_thread(os.chmod, temporary, 0o600)
    await asyncio.to_thread(os.replace, temporary, target)
    return len(cookies)


async def restore_session_cookies(endpoint, root):
    target = _snapshot_path(root)
    try:
        payload = json.loads(await asyncio.to_thread(target.read_text, encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as exc:
        raise RuntimeError("browser_cookie_snapshot_invalid") from exc
    cookies = payload.get("cookies") if isinstance(payload, dict) and payload.get("version") == 1 else None
    if not isinstance(cookies, list):
        raise RuntimeError("browser_cookie_snapshot_invalid")
    if not cookies:
        return 0
    client, ws = await _open_browser_ws(endpoint)
    try:
        await _cdp_call(ws, 1, "Storage.setCookies", {"cookies": cookies})
    finally:
        await ws.close()
        await client.close()
    return len(cookies)


async def reload_http_pages(endpoint):
    client, ws = await _open_browser_ws(endpoint)
    request_id = 1
    reloaded = 0
    try:
        targets = await _cdp_call(ws, request_id, "Target.getTargets")
        request_id += 1
        for target in targets.get("targetInfos") or []:
            if target.get("type") != "page":
                continue
            url = str(target.get("url") or "")
            if not url.startswith(("http://", "https://")):
                continue
            attached = await _cdp_call(
                ws,
                request_id,
                "Target.attachToTarget",
                {"targetId": target["targetId"], "flatten": True},
            )
            request_id += 1
            session_id = attached.get("sessionId")
            if not session_id:
                continue
            await _cdp_call(ws, request_id, "Page.reload", {}, session_id=session_id)
            request_id += 1
            reloaded += 1
    finally:
        await ws.close()
        await client.close()
    if reloaded:
        await asyncio.sleep(0.75)
    return reloaded
