"""Human-in-the-loop controls for the private VELIA Browser Agent."""
from urllib.parse import urlsplit

from agent_core.browser.state_store import _cdp_call, _open_browser_ws

_ALLOWED_KEYS = {
    "Enter": ("Enter", 13),
    "Tab": ("Tab", 9),
    "Escape": ("Escape", 27),
    "Backspace": ("Backspace", 8),
    "ArrowUp": ("ArrowUp", 38),
    "ArrowDown": ("ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", 39),
}


async def _active_page(ws):
    request_id = 1
    targets = await _cdp_call(ws, request_id, "Target.getTargets")
    request_id += 1
    fallback = None
    for target in targets.get("targetInfos") or []:
        if target.get("type") != "page":
            continue
        url = str(target.get("url") or "")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        attached = await _cdp_call(
            ws, request_id, "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        request_id += 1
        session_id = attached.get("sessionId")
        if not session_id:
            continue
        info = await _cdp_call(
            ws, request_id, "Runtime.evaluate",
            {
                "expression": (
                    "({url:location.href,title:document.title,"
                    "visibility:document.visibilityState,focused:document.hasFocus(),"
                    "width:innerWidth,height:innerHeight})"
                ),
                "returnByValue": True,
            },
            session_id=session_id,
        )
        request_id += 1
        value = ((info.get("result") or {}).get("value") or {})
        row = (session_id, value, request_id)
        fallback = row
        if value.get("focused") is True:
            return row
    if fallback is None:
        raise RuntimeError("browser_takeover_page_unavailable")
    return fallback


async def capture_takeover_state(endpoint):
    client, ws = await _open_browser_ws(endpoint)
    try:
        session_id, info, request_id = await _active_page(ws)
        shot = await _cdp_call(
            ws, request_id, "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
            session_id=session_id,
        )
        image = shot.get("data")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if not isinstance(image, str) or not image or width < 1 or height < 1:
            raise RuntimeError("browser_takeover_capture_failed")
        return {
            "image": image,
            "width": min(width, 10000),
            "height": min(height, 10000),
            "url": str(info.get("url") or "")[:4096],
            "title": str(info.get("title") or "")[:1024],
        }
    finally:
        await ws.close()
        await client.close()


async def takeover_click(endpoint, x, y):
    client, ws = await _open_browser_ws(endpoint)
    try:
        session_id, info, request_id = await _active_page(ws)
        width = max(1, int(info.get("width") or 1))
        height = max(1, int(info.get("height") or 1))
        x = max(0.0, min(float(x), float(width - 1)))
        y = max(0.0, min(float(y), float(height - 1)))
        for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
            params = {"type": event_type, "x": x, "y": y}
            if event_type != "mouseMoved":
                params.update({"button": "left", "clickCount": 1})
            await _cdp_call(
                ws, request_id, "Input.dispatchMouseEvent", params,
                session_id=session_id,
            )
            request_id += 1
    finally:
        await ws.close()
        await client.close()


async def takeover_insert_text(endpoint, text):
    if not isinstance(text, str) or not text or len(text) > 4096:
        raise ValueError("invalid_takeover_text")
    client, ws = await _open_browser_ws(endpoint)
    try:
        session_id, _info, request_id = await _active_page(ws)
        await _cdp_call(
            ws, request_id, "Input.insertText", {"text": text},
            session_id=session_id,
        )
    finally:
        await ws.close()
        await client.close()


async def takeover_press_key(endpoint, key):
    if key not in _ALLOWED_KEYS:
        raise ValueError("invalid_takeover_key")
    code, vk = _ALLOWED_KEYS[key]
    client, ws = await _open_browser_ws(endpoint)
    try:
        session_id, _info, request_id = await _active_page(ws)
        params = {"key": key, "code": code, "windowsVirtualKeyCode": vk}
        await _cdp_call(
            ws, request_id, "Input.dispatchKeyEvent",
            {"type": "keyDown", **params}, session_id=session_id,
        )
        await _cdp_call(
            ws, request_id + 1, "Input.dispatchKeyEvent",
            {"type": "keyUp", **params}, session_id=session_id,
        )
    finally:
        await ws.close()
        await client.close()


async def takeover_scroll(endpoint, delta_y):
    delta = max(-2000.0, min(float(delta_y), 2000.0))
    client, ws = await _open_browser_ws(endpoint)
    try:
        session_id, info, request_id = await _active_page(ws)
        await _cdp_call(
            ws, request_id, "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": max(0, int(info.get("width") or 1) // 2),
                "y": max(0, int(info.get("height") or 1) // 2),
                "deltaX": 0,
                "deltaY": delta,
            },
            session_id=session_id,
        )
    finally:
        await ws.close()
        await client.close()
