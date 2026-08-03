import json
import os
from typing import Any

from aiohttp import web


_ME_ROUTE = "/mobile-api/v1/me"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _route_canonical(route: Any) -> str:
    resource = getattr(route, "resource", None)
    canonical = str(getattr(resource, "canonical", "") or "")
    if canonical:
        return canonical
    try:
        info = route.get_info() or {}
    except Exception:
        info = {}
    return str(info.get("formatter") or info.get("path") or "")


def install(app: web.Application, routes_module: Any) -> None:
    if app.get("velia_attachment_feature_flag_installed"):
        return

    for route in app.router.routes():
        if str(getattr(route, "method", "")).upper() != "GET":
            continue
        if _route_canonical(route) != _ME_ROUTE:
            continue
        original_handler = route.handler

        async def handle_me_with_file_flag(request: web.Request) -> web.StreamResponse:
            response = await original_handler(request)
            if not isinstance(response, web.Response) or response.status != 200:
                return response
            try:
                data = json.loads(response.text or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                return response
            if not isinstance(data, dict) or not data.get("ok"):
                return response
            features = data.get("features")
            if not isinstance(features, dict):
                features = {}
                data["features"] = features
            features["file_analyst"] = _env_bool(
                "VELIA_FILE_ANALYST_ENABLED",
                False,
            )
            return routes_module._json_response(data, status=response.status)

        route._handler = handle_me_with_file_flag
        app["velia_attachment_feature_flag_installed"] = True
        return
    raise RuntimeError("VELIA mobile me route is unavailable")
