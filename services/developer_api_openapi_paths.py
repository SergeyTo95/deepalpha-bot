from collections import OrderedDict
from typing import Any, Dict, List, Optional


def _secured(scope: str) -> Dict[str, Any]:
    return {
        "security": [{"bearerAuth": []}],
        "x-required-scopes": [scope],
    }


def _operation(
    tag: str,
    operation_id: str,
    summary: str,
    *,
    scope: Optional[str] = None,
    description: str = "",
    parameters: Optional[List[Dict[str, Any]]] = None,
    request_schema: Optional[str] = None,
    request_example: Optional[Dict[str, Any]] = None,
    responses: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "tags": [tag],
        "operationId": operation_id,
        "summary": summary,
        "responses": responses or {},
    }
    if scope:
        payload.update(_secured(scope))
    if description:
        payload["description"] = description
    if parameters:
        payload["parameters"] = parameters
    if request_schema:
        content: Dict[str, Any] = {
            "schema": {"$ref": f"#/components/schemas/{request_schema}"}
        }
        if request_example is not None:
            content["example"] = request_example
        payload["requestBody"] = {
            "required": True,
            "content": {"application/json": content},
        }
    return payload


def _success(schema: str, description: str) -> Dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema}"}
            }
        },
    }


def _common_errors() -> Dict[str, Any]:
    return {
        "401": {"$ref": "#/components/responses/Unauthorized"},
        "403": {"$ref": "#/components/responses/Forbidden"},
        "429": {"$ref": "#/components/responses/RateLimited"},
        "503": {"$ref": "#/components/responses/ServiceUnavailable"},
    }


def build_paths() -> Dict[str, Any]:
    paths: Dict[str, Any] = OrderedDict()

    paths["/api/v1/health"] = {
        "get": _operation(
            "System",
            "getHealth",
            "Get Developer API runtime health",
            responses={
                "200": _success("HealthResponse", "Operational or degraded runtime health."),
                "503": {"$ref": "#/components/responses/ServiceUnavailable"},
            },
        )
    }

    paths["/api/v1/account"] = {
        "get": _operation(
            "Account",
            "getAccount",
            "Get the authenticated API client",
            scope="account:read",
            responses={
                "200": _success("AccountResponse", "Client, balance, scopes, and current limits."),
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/usage"] = {
        "get": _operation(
            "Account",
            "getUsage",
            "Get usage totals for the authenticated key",
            scope="usage:read",
            responses={
                "200": _success("UsageResponse", "Usage summary."),
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/capabilities"] = {
        "get": _operation(
            "Account",
            "getCapabilities",
            "Get enabled API capabilities",
            scope="account:read",
            responses={
                "200": _success(
                    "CapabilitiesResponse",
                    "Enabled endpoints, scopes, products, limits, and webhook events.",
                ),
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/analyses"] = {
        "post": _operation(
            "Quick Analysis",
            "createQuickAnalysis",
            "Start a billed Quick Analysis",
            scope="analysis:run",
            description=(
                "Atomically reserves the current quick_analysis price and queues a durable job. "
                "A matching idempotent replay returns the original job without a second reservation."
            ),
            parameters=[
                {"$ref": "#/components/parameters/IdempotencyKey"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            request_schema="QuickAnalysisRequest",
            request_example={
                "market_url": "https://polymarket.com/event/example-market",
                "mode": "quick",
                "language": "en",
            },
            responses={
                "200": _success("JobSubmissionResponse", "Matching idempotent replay."),
                "202": _success("JobSubmissionResponse", "New job accepted and credits reserved."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "402": {"$ref": "#/components/responses/PaymentRequired"},
                "403": {"$ref": "#/components/responses/Forbidden"},
                "409": {"$ref": "#/components/responses/Conflict"},
                "429": {"$ref": "#/components/responses/RateLimited"},
                "503": {"$ref": "#/components/responses/ServiceUnavailable"},
            },
        )
    }

    paths["/api/v1/analyses/{job_id}"] = {
        "get": _operation(
            "Quick Analysis",
            "getQuickAnalysis",
            "Get a Quick Analysis job",
            scope="analysis:read",
            description="Returns only jobs owned by the authenticated API client.",
            parameters=[
                {"$ref": "#/components/parameters/JobId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": _success("QuickAnalysisJob", "Queued, running, successful, or failed job."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "404": {"$ref": "#/components/responses/NotFound"},
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/opportunity-scans"] = {
        "post": _operation(
            "Opportunity Scan",
            "createOpportunityScan",
            "Start a billed zero-LLM Opportunity Scan",
            scope="opportunities:run",
            description=(
                "Ranks public Polymarket markets for later analysis. It does not call paid AI "
                "providers and does not calculate fair probability, edge, or a BUY signal."
            ),
            parameters=[
                {"$ref": "#/components/parameters/IdempotencyKey"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            request_schema="OpportunityScanRequest",
            request_example={
                "category": "All",
                "language": "en",
                "scan_limit": 100,
                "result_limit": 10,
                "min_score": 52,
                "min_liquidity": 1000,
                "min_volume_24h": 500,
                "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"],
            },
            responses={
                "200": _success("JobSubmissionResponse", "Matching idempotent replay."),
                "202": _success("JobSubmissionResponse", "New scan accepted and credits reserved."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "401": {"$ref": "#/components/responses/Unauthorized"},
                "402": {"$ref": "#/components/responses/PaymentRequired"},
                "403": {"$ref": "#/components/responses/Forbidden"},
                "409": {"$ref": "#/components/responses/Conflict"},
                "429": {"$ref": "#/components/responses/RateLimited"},
                "503": {"$ref": "#/components/responses/ServiceUnavailable"},
            },
        )
    }

    paths["/api/v1/opportunity-scans/{job_id}"] = {
        "get": _operation(
            "Opportunity Scan",
            "getOpportunityScan",
            "Get an Opportunity Scan job",
            scope="opportunities:read",
            description="Returns only scans owned by the authenticated API client.",
            parameters=[
                {"$ref": "#/components/parameters/JobId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": _success("OpportunityScanJob", "Queued, running, successful, or failed scan."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "404": {"$ref": "#/components/responses/NotFound"},
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/webhooks"] = {
        "post": _operation(
            "Signed Webhooks",
            "createWebhook",
            "Create a signed webhook endpoint",
            scope="webhooks:manage",
            description=(
                "The signing secret is returned once. Targets must resolve only to public IPs "
                "and use HTTPS port 443."
            ),
            parameters=[{"$ref": "#/components/parameters/RequestId"}],
            request_schema="WebhookCreateRequest",
            request_example={
                "name": "production",
                "url": "https://example.com/deepalpha/webhook",
                "events": [
                    "analysis.completed",
                    "analysis.failed",
                    "opportunity_scan.completed",
                    "opportunity_scan.failed",
                ],
            },
            responses={
                "201": _success("WebhookCreateResponse", "Webhook created. Save signing_secret now."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "409": {"$ref": "#/components/responses/Conflict"},
                **_common_errors(),
            },
        ),
        "get": _operation(
            "Signed Webhooks",
            "listWebhooks",
            "List webhook endpoints",
            scope="webhooks:manage",
            parameters=[{"$ref": "#/components/parameters/RequestId"}],
            responses={
                "200": _success("WebhookListResponse", "Webhook endpoints without signing secrets."),
                **_common_errors(),
            },
        ),
    }

    paths["/api/v1/webhooks/{webhook_id}"] = {
        "delete": _operation(
            "Signed Webhooks",
            "disableWebhook",
            "Disable a webhook endpoint",
            scope="webhooks:manage",
            parameters=[
                {"$ref": "#/components/parameters/WebhookId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": {
                    "description": "Webhook disabled.",
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["ok", "status"],
                                "properties": {
                                    "ok": {"type": "boolean", "const": True},
                                    "request_id": {"type": "string"},
                                    "status": {"type": "string", "const": "disabled"},
                                },
                            }
                        }
                    },
                },
                "404": {"$ref": "#/components/responses/NotFound"},
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/webhooks/{webhook_id}/rotate-secret"] = {
        "post": _operation(
            "Signed Webhooks",
            "rotateWebhookSecret",
            "Rotate a webhook signing secret",
            scope="webhooks:manage",
            description=(
                "Returns the new signing secret once and invalidates the previous secret immediately."
            ),
            parameters=[
                {"$ref": "#/components/parameters/WebhookId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": _success("WebhookCreateResponse", "Secret rotated. Save signing_secret now."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "404": {"$ref": "#/components/responses/NotFound"},
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/webhook-deliveries"] = {
        "get": _operation(
            "Signed Webhooks",
            "listWebhookDeliveries",
            "List webhook deliveries",
            scope="webhooks:manage",
            parameters=[
                {
                    "name": "limit",
                    "in": "query",
                    "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                },
                {
                    "name": "status",
                    "in": "query",
                    "schema": {
                        "type": "string",
                        "enum": ["pending", "retrying", "delivering", "succeeded", "failed"],
                    },
                },
                {
                    "name": "webhook_id",
                    "in": "query",
                    "schema": {"type": "string", "pattern": "^wh_[0-9a-f]{32}$"},
                },
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": _success("WebhookDeliveryListResponse", "Delivery journal summary."),
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/webhook-deliveries/{delivery_id}"] = {
        "get": _operation(
            "Signed Webhooks",
            "getWebhookDelivery",
            "Get one delivery and its attempts",
            scope="webhooks:manage",
            parameters=[
                {"$ref": "#/components/parameters/DeliveryId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "200": _success("WebhookDeliveryDetailResponse", "Delivery with capped attempt journal."),
                "404": {"$ref": "#/components/responses/NotFound"},
                **_common_errors(),
            },
        )
    }

    paths["/api/v1/webhook-deliveries/{delivery_id}/retry"] = {
        "post": _operation(
            "Signed Webhooks",
            "retryWebhookDelivery",
            "Retry a terminal webhook delivery",
            scope="webhooks:manage",
            parameters=[
                {"$ref": "#/components/parameters/DeliveryId"},
                {"$ref": "#/components/parameters/RequestId"},
            ],
            responses={
                "202": _success("WebhookDeliveryResponse", "Delivery queued for a manual retry."),
                "400": {"$ref": "#/components/responses/BadRequest"},
                "409": {"$ref": "#/components/responses/Conflict"},
                **_common_errors(),
            },
        )
    }

    return paths
