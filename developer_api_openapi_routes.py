import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from aiohttp import web

_DOCS_DIR = Path(__file__).resolve().parent / "docs"
_OPENAPI_PATH = _DOCS_DIR / "openapi.json"
_POSTMAN_PATH = _DOCS_DIR / "deepalpha_api.postman_collection.json"

_SWAGGER_UI_VERSION = "5.17.14"
_SWAGGER_HTML = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>DeepAlpha Developer API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{_SWAGGER_UI_VERSION}/swagger-ui.css">
  <style>
    html {{ box-sizing: border-box; overflow-y: scroll; }}
    *, *::before, *::after {{ box-sizing: inherit; }}
    body {{ margin: 0; background: #0b1020; }}
    .topbar {{ display: none; }}
    .swagger-ui {{ color: #111827; }}
    .swagger-ui .info {{ margin: 32px 0 18px; }}
    .docs-links {{ position: sticky; top: 0; z-index: 20; display: flex; gap: 10px; flex-wrap: wrap; padding: 10px 16px; background: #0f172a; border-bottom: 1px solid #243047; font: 14px Arial, sans-serif; }}
    .docs-links a {{ color: #bfdbfe; text-decoration: none; padding: 7px 10px; border: 1px solid #334155; border-radius: 8px; }}
  </style>
</head>
<body>
  <nav class="docs-links" aria-label="API documentation downloads">
    <a href="/api/openapi.json">OpenAPI JSON</a>
    <a href="/api/postman.json">Postman collection</a>
    <a href="/developer">Developer Portal</a>
  </nav>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{_SWAGGER_UI_VERSION}/swagger-ui-bundle.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@{_SWAGGER_UI_VERSION}/swagger-ui-standalone-preset.js"></script>
  <script>
    window.addEventListener('load', function () {{
      window.ui = SwaggerUIBundle({{
        url: '/api/openapi.json',
        dom_id: '#swagger-ui',
        deepLinking: true,
        displayRequestDuration: true,
        filter: true,
        persistAuthorization: true,
        tryItOutEnabled: true,
        docExpansion: 'list',
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        layout: 'StandaloneLayout'
      }});
    }});
  </script>
</body>
</html>"""


def _asset(path: Path) -> Tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    # Parse once at process lifetime so malformed committed artifacts fail loudly
    # during tests and return a stable 503 in a broken production deploy.
    json.loads(text)
    etag = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, etag


@lru_cache(maxsize=1)
def _openapi_asset() -> Tuple[str, str]:
    return _asset(_OPENAPI_PATH)


@lru_cache(maxsize=1)
def _postman_asset() -> Tuple[str, str]:
    return _asset(_POSTMAN_PATH)


def _json_asset_response(request: web.Request, loader) -> web.Response:
    try:
        text, etag = loader()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "documentation_unavailable"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )
    quoted = f'"{etag}"'
    if request.headers.get("If-None-Match") == quoted:
        return web.Response(status=304, headers={"ETag": quoted, "Cache-Control": "public, max-age=300"})
    return web.Response(
        text=text,
        content_type="application/json",
        headers={
            "ETag": quoted,
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def handle_openapi_json(request: web.Request) -> web.Response:
    return _json_asset_response(request, _openapi_asset)


async def handle_postman_collection(request: web.Request) -> web.Response:
    response = _json_asset_response(request, _postman_asset)
    if response.status == 200:
        response.headers["Content-Disposition"] = 'inline; filename="deepalpha_api.postman_collection.json"'
    return response


async def handle_swagger_ui(_request: web.Request) -> web.Response:
    return web.Response(
        text=_SWAGGER_HTML,
        content_type="text/html",
        headers={
            "Cache-Control": "public, max-age=300",
            "Content-Security-Policy": (
                "default-src 'none'; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https:; "
                "font-src 'self' data: https://cdn.jsdelivr.net; "
                "connect-src 'self'; frame-ancestors 'self'; base-uri 'none'"
            ),
        },
    )


async def handle_docs_options(_request: web.Request) -> web.Response:
    return web.Response(status=204)


def setup_developer_api_openapi_routes(app: web.Application) -> None:
    if app.get("developer_api_openapi_routes_installed"):
        return
    app.router.add_get("/api/openapi.json", handle_openapi_json)
    app.router.add_get("/api/postman.json", handle_postman_collection)
    app.router.add_get("/api/docs", handle_swagger_ui)
    app.router.add_get("/api/docs/", handle_swagger_ui)
    for path in ("/api/openapi.json", "/api/postman.json", "/api/docs", "/api/docs/"):
        app.router.add_route("OPTIONS", path, handle_docs_options)
    app["developer_api_openapi_routes_installed"] = True
