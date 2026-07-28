import hashlib
import json
from functools import lru_cache
from typing import Any, Dict, Tuple

from services.developer_api_openapi_components import build_components
from services.developer_api_openapi_paths import build_paths


@lru_cache(maxsize=1)
def build_openapi_spec() -> Dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "info": {
            "title": "DeepAlpha Developer API",
            "version": "1.0.0-beta",
            "summary": "Billed Polymarket analysis and zero-LLM market triage.",
            "description": (
                "DeepAlpha Developer API provides durable Quick Analysis and Opportunity Scan jobs, "
                "atomic API-credit settlement, scoped bearer keys, and HMAC-signed terminal webhooks.\n\n"
                "Use da_test_ keys for test projects. Administrator-approved live projects may issue "
                "da_live_ keys in the authenticated Developer Portal. Rotation preserves the key environment.\n\n"
                "API credits are separate from Telegram user tokens. Quick Analysis costs 10 credits "
                "and Opportunity Scan costs 1 credit by default; administrators may change product prices. "
                "Daily and monthly credit-spend caps can reject new reservations with stable error codes.\n\n"
                "Credit packages, invoices, live approval, and billing controls are cookie-authenticated Portal "
                "operations and are intentionally excluded from this bearer API contract. Wallet send, wallet "
                "withdrawal, trading execution, Deep Analysis execution, and automatic recharge are unavailable."
            ),
            "contact": {"name": "DeepAlpha API"},
            "license": {"name": "Proprietary"},
        },
        "servers": [{"url": "/", "description": "Current DeepAlpha deployment"}],
        "tags": [
            {"name": "System", "description": "Public runtime health."},
            {"name": "Account", "description": "Client, capabilities, limits, spend controls, and usage."},
            {"name": "Quick Analysis", "description": "Billed AI analysis of one Polymarket market."},
            {"name": "Opportunity Scan", "description": "Billed deterministic zero-LLM market triage."},
            {"name": "Signed Webhooks", "description": "HMAC-signed terminal events and retry journal."},
        ],
        "paths": build_paths(),
        "components": build_components(),
        "x-documentation-endpoints": {
            "swagger_ui": "/api/docs",
            "openapi_json": "/api/openapi.json",
            "postman_collection": "/api/postman.json",
        },
        "x-commercial-portal": {
            "authentication": "DeepAlpha web session plus X-DeepAlpha-Portal: 1 for mutations",
            "document": "docs/api_commercial_launch.md",
            "included_in_bearer_openapi": False,
        },
    }


@lru_cache(maxsize=1)
def serialized_openapi_spec() -> Tuple[str, str]:
    text = json.dumps(
        build_openapi_spec(),
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    etag = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, etag
