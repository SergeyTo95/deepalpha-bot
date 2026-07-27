import json
import logging
from typing import Any, Dict

import developer_api_routes as routes

logger = logging.getLogger(__name__)

_DOCUMENTATION = {
    "swagger_ui": "/api/docs",
    "openapi_json": "/api/openapi.json",
    "postman_collection": "/api/postman.json",
}


def install() -> None:
    original = routes.handle_developer_api_capabilities
    if getattr(original, "_deepalpha_openapi_docs", False):
        return

    async def capabilities_with_documentation(request):
        response = await original(request)
        if response.status != 200:
            return response
        try:
            payload: Dict[str, Any] = json.loads(response.text or "{}")
        except Exception:
            return response

        available = list(payload.get("available_endpoints") or [])
        for endpoint in (
            "GET /api/docs",
            "GET /api/openapi.json",
            "GET /api/postman.json",
        ):
            if endpoint not in available:
                available.append(endpoint)
        payload["available_endpoints"] = available
        payload["documentation"] = dict(_DOCUMENTATION)
        payload["openapi_version"] = "3.1.0"
        payload["planned_endpoints"] = [
            item
            for item in list(payload.get("planned_endpoints") or [])
            if "OpenAPI" not in str(item) and "Swagger" not in str(item)
        ]
        return routes._json_response(payload)

    capabilities_with_documentation._deepalpha_openapi_docs = True
    capabilities_with_documentation._deepalpha_original = original
    routes.handle_developer_api_capabilities = capabilities_with_documentation
    logger.info("DEVELOPER_API_OPENAPI_CAPABILITIES_PATCH_INSTALLED")
