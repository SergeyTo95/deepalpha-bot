from typing import Any, Dict


def _error_response(description: str, code: str) -> Dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                "example": {
                    "ok": False,
                    "error": code,
                    "request_id": "req_example",
                },
            }
        },
    }


def build_components() -> Dict[str, Any]:
    webhook_events = [
        "analysis.completed",
        "analysis.failed",
        "opportunity_scan.completed",
        "opportunity_scan.failed",
    ]
    opportunity_tiers = [
        "DEEP_ANALYSIS_CANDIDATE",
        "WATCH_CANDIDATE",
        "LOW_PRIORITY",
    ]
    job_statuses = ["queued", "running", "success", "error"]

    return {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "DeepAlpha API key",
                "description": "Authorization: Bearer da_test_... or da_live_...",
            }
        },
        "parameters": {
            "IdempotencyKey": {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "description": "Unique per API client. Reuse only for the identical canonical request.",
                "schema": {"type": "string", "minLength": 1, "maxLength": 200},
                "example": "request_01JEXAMPLE",
            },
            "RequestId": {
                "name": "X-Request-ID",
                "in": "header",
                "required": False,
                "description": "Optional caller-provided correlation ID.",
                "schema": {
                    "type": "string",
                    "maxLength": 100,
                    "pattern": "^[A-Za-z0-9_.:-]+$",
                },
            },
            "JobId": {
                "name": "job_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "pattern": "^job_[0-9a-f]{32}$"},
            },
            "WebhookId": {
                "name": "webhook_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "pattern": "^wh_[0-9a-f]{32}$"},
            },
            "DeliveryId": {
                "name": "delivery_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "pattern": "^delivery_[0-9a-f]{32}$"},
            },
        },
        "responses": {
            "BadRequest": _error_response(
                "Invalid JSON, validation error, missing idempotency key, or unsupported field.",
                "invalid_request",
            ),
            "Unauthorized": {
                **_error_response("Missing or invalid API key.", "invalid_api_key"),
                "headers": {
                    "WWW-Authenticate": {
                        "schema": {"type": "string"},
                        "example": "Bearer",
                    }
                },
            },
            "PaymentRequired": _error_response(
                "The API client does not have enough credits.",
                "insufficient_api_credits",
            ),
            "Forbidden": _error_response(
                "The key does not include the required scope.",
                "insufficient_scope",
            ),
            "NotFound": _error_response(
                "The resource does not exist or is not owned by this API client.",
                "not_found",
            ),
            "Conflict": _error_response(
                "Idempotency conflict, active-job limit, disabled product, or non-retryable delivery.",
                "idempotency_conflict",
            ),
            "RateLimited": {
                **_error_response(
                    "Per-minute, daily, or monthly limit exceeded.",
                    "rate_limit_exceeded",
                ),
                "headers": {"Retry-After": {"schema": {"type": "integer", "minimum": 1}}},
            },
            "ServiceUnavailable": _error_response(
                "Database, worker, or internal service is unavailable.",
                "service_unavailable",
            ),
        },
        "schemas": {
            "ErrorResponse": {
                "type": "object",
                "required": ["ok", "error"],
                "properties": {
                    "ok": {"type": "boolean", "const": False},
                    "error": {"type": "string", "description": "Stable machine-readable error code."},
                    "request_id": {"type": "string"},
                    "required_scope": {"type": "string"},
                    "details": {"type": "object", "additionalProperties": True},
                },
                "additionalProperties": True,
            },
            "HealthResponse": {
                "type": "object",
                "required": ["ok", "service", "version", "status"],
                "properties": {
                    "ok": {"type": "boolean"},
                    "service": {"type": "string", "const": "deepalpha-developer-api"},
                    "version": {"type": "string", "const": "v1"},
                    "status": {
                        "type": "string",
                        "enum": ["operational", "degraded", "unavailable"],
                    },
                    "database": {"type": "object", "additionalProperties": True},
                    "worker": {"type": "object", "additionalProperties": True},
                    "queue": {"type": "object", "additionalProperties": True},
                    "recent": {"type": "object", "additionalProperties": True},
                    "webhooks": {"type": "object", "additionalProperties": True},
                    "opportunity_scans": {"type": "object", "additionalProperties": True},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                    "checked_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": True,
            },
            "AccountResponse": {
                "type": "object",
                "required": ["ok", "request_id", "client", "limits"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "client": {
                        "type": "object",
                        "required": [
                            "id",
                            "name",
                            "environment",
                            "key_prefix",
                            "scopes",
                            "credit_balance",
                        ],
                        "properties": {
                            "id": {"type": "integer", "minimum": 1},
                            "name": {"type": "string"},
                            "environment": {"type": "string", "enum": ["test", "live"]},
                            "key_prefix": {"type": "string"},
                            "scopes": {"type": "array", "items": {"type": "string"}},
                            "credit_balance": {"type": "integer"},
                        },
                    },
                    "limits": {"type": "object", "additionalProperties": True},
                },
            },
            "UsageResponse": {
                "type": "object",
                "required": ["ok", "request_id", "usage"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "usage": {"type": "object", "additionalProperties": True},
                },
            },
            "CapabilitiesResponse": {
                "type": "object",
                "required": ["ok", "request_id", "available_scopes", "available_endpoints"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "available_scopes": {"type": "array", "items": {"type": "string"}},
                    "available_endpoints": {"type": "array", "items": {"type": "string"}},
                    "planned_endpoints": {"type": "array", "items": {"type": "string"}},
                    "available_analysis_modes": {"type": "array", "items": {"type": "string"}},
                    "webhook_events": {"type": "array", "items": {"type": "string"}},
                    "opportunity_scan": {"type": "object", "additionalProperties": True},
                    "documentation": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "additionalProperties": True,
            },
            "QuickAnalysisRequest": {
                "type": "object",
                "required": ["market_url"],
                "properties": {
                    "market_url": {
                        "type": "string",
                        "format": "uri",
                        "pattern": "^https://(www\\.)?polymarket\\.com/(event|market)/",
                    },
                    "mode": {"type": "string", "const": "quick", "default": "quick"},
                    "language": {
                        "type": "string",
                        "enum": ["en", "ru"],
                        "default": "en",
                    },
                },
                "additionalProperties": False,
            },
            "OpportunityScanRequest": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["All", "Crypto", "Politics", "Sports", "Economy", "Tech", "Other"],
                        "default": "All",
                    },
                    "language": {"type": "string", "enum": ["en", "ru"], "default": "en"},
                    "scan_limit": {"type": "integer", "minimum": 10, "maximum": 200, "default": 100},
                    "result_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                    "min_score": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
                    "min_liquidity": {"type": "number", "minimum": 0, "default": 0},
                    "min_volume_24h": {"type": "number", "minimum": 0, "default": 0},
                    "tiers": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string", "enum": opportunity_tiers},
                    },
                },
                "additionalProperties": False,
            },
            "JobSubmissionResponse": {
                "type": "object",
                "required": [
                    "ok",
                    "request_id",
                    "job_id",
                    "status",
                    "idempotent",
                    "credits_reserved",
                    "credit_balance",
                    "status_url",
                ],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "job_id": {"type": "string", "pattern": "^job_[0-9a-f]{32}$"},
                    "status": {"type": "string", "enum": job_statuses},
                    "job_type": {"type": "string"},
                    "analysis_type": {"type": "string"},
                    "mode": {"type": "string"},
                    "idempotent": {"type": "boolean"},
                    "credits_reserved": {"type": "integer", "minimum": 0},
                    "credit_balance": {"type": "integer"},
                    "status_url": {"type": "string"},
                },
                "additionalProperties": True,
            },
            "JobCredits": {
                "type": "object",
                "required": ["reserved", "charged", "refunded", "reservation_status"],
                "properties": {
                    "reserved": {"type": "integer", "minimum": 0},
                    "charged": {"type": "integer", "minimum": 0},
                    "refunded": {"type": "integer", "minimum": 0},
                    "reservation_status": {
                        "type": ["string", "null"],
                        "enum": ["reserved", "charged", "refunded", None],
                    },
                },
            },
            "QuickAnalysisResult": {
                "type": "object",
                "required": ["schema_version", "question", "decision", "generated_at"],
                "properties": {
                    "schema_version": {"type": "string", "const": "1.0"},
                    "question": {"type": "string"},
                    "market_url": {"type": "string", "format": "uri"},
                    "market_slug": {"type": "string"},
                    "decision": {"type": "string", "enum": ["BUY", "WATCH", "WAIT", "NO_TRADE"]},
                    "side": {"type": ["string", "null"], "enum": ["YES", "NO", None]},
                    "fair_probability": {"type": ["number", "null"]},
                    "market_probability": {"type": ["number", "null"]},
                    "edge": {"type": ["number", "null"]},
                    "confidence": {"type": ["number", "string", "null"]},
                    "data_quality": {"type": ["string", "null"]},
                    "summary": {"type": "string"},
                    "reasoning": {"type": ["string", "array"], "items": {"type": "string"}},
                    "factors": {"type": "array", "items": {"type": "string"}},
                    "risks": {"type": "array", "items": {"type": "string"}},
                    "sources": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    "analysis": {"type": "string"},
                    "generated_at": {"type": "string", "format": "date-time"},
                },
                "additionalProperties": True,
            },
            "QuickAnalysisJob": {
                "type": "object",
                "required": ["ok", "job_id", "status", "progress", "request", "credits"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "status": {"type": "string", "enum": job_statuses},
                    "analysis_type": {"type": "string", "const": "quick"},
                    "mode": {"type": "string", "const": "quick"},
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                    "request": {"$ref": "#/components/schemas/QuickAnalysisRequest"},
                    "credits": {"$ref": "#/components/schemas/JobCredits"},
                    "result": {"$ref": "#/components/schemas/QuickAnalysisResult"},
                    "error": {"type": "string"},
                    "created_at": {"type": ["string", "null"], "format": "date-time"},
                    "started_at": {"type": ["string", "null"], "format": "date-time"},
                    "finished_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": True,
            },
            "OpportunityCandidate": {
                "type": "object",
                "required": ["market_id", "question", "url", "score", "tier"],
                "properties": {
                    "market_id": {"type": "string"},
                    "event_key": {"type": "string"},
                    "question": {"type": "string"},
                    "url": {"type": "string", "format": "uri"},
                    "category": {"type": "string"},
                    "yes_price": {"type": "number"},
                    "no_price": {"type": "number"},
                    "liquidity": {"type": "number"},
                    "volume_24h": {"type": "number"},
                    "volume_total": {"type": "number"},
                    "hours_to_close": {"type": ["number", "null"]},
                    "price_move_24h_pp": {"type": "number"},
                    "event_market_count": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "tier": {"type": "string", "enum": opportunity_tiers},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "risk_flags": {"type": "array", "items": {"type": "string"}},
                    "score_components": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                },
                "additionalProperties": False,
            },
            "OpportunityScanResult": {
                "type": "object",
                "required": [
                    "schema_version",
                    "scan_type",
                    "provider_calls",
                    "paid_ai_used",
                    "candidate_count",
                    "candidates",
                    "generated_at",
                    "disclaimer",
                ],
                "properties": {
                    "schema_version": {"type": "string", "const": "1.0"},
                    "scan_type": {"type": "string", "const": "opportunity_scan"},
                    "provider_calls": {"type": "integer", "const": 0},
                    "paid_ai_used": {"type": "boolean", "const": False},
                    "category": {"type": "string"},
                    "language": {"type": "string", "enum": ["en", "ru"]},
                    "filters": {"type": "object", "additionalProperties": True},
                    "markets_received": {"type": "integer"},
                    "eligible_markets": {"type": "integer"},
                    "candidate_count": {"type": "integer", "maximum": 20},
                    "candidates": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/OpportunityCandidate"},
                    },
                    "rejection_counts": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                    "source_cached": {"type": "boolean"},
                    "generated_at": {"type": "string", "format": "date-time"},
                    "disclaimer": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "OpportunityScanJob": {
                "type": "object",
                "required": ["ok", "job_id", "status", "job_type", "progress", "request", "credits"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "status": {"type": "string", "enum": job_statuses},
                    "job_type": {"type": "string", "const": "opportunity_scan"},
                    "progress": {"type": "integer", "minimum": 0, "maximum": 100},
                    "request": {"$ref": "#/components/schemas/OpportunityScanRequest"},
                    "credits": {"$ref": "#/components/schemas/JobCredits"},
                    "result": {"$ref": "#/components/schemas/OpportunityScanResult"},
                    "error": {"type": "string"},
                    "created_at": {"type": ["string", "null"], "format": "date-time"},
                    "started_at": {"type": ["string", "null"], "format": "date-time"},
                    "finished_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": True,
            },
            "WebhookEvent": {"type": "string", "enum": webhook_events},
            "WebhookCreateRequest": {
                "type": "object",
                "required": ["url", "events"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80, "default": "default"},
                    "url": {"type": "string", "format": "uri", "pattern": "^https://"},
                    "events": {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"$ref": "#/components/schemas/WebhookEvent"},
                    },
                },
                "additionalProperties": False,
            },
            "Webhook": {
                "type": "object",
                "required": ["webhook_id", "name", "url", "events", "status"],
                "properties": {
                    "webhook_id": {"type": "string"},
                    "name": {"type": "string"},
                    "url": {"type": "string", "format": "uri"},
                    "events": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/WebhookEvent"},
                    },
                    "status": {"type": "string", "enum": ["active", "disabled"]},
                    "consecutive_failures": {"type": "integer"},
                    "last_success_at": {"type": ["string", "null"], "format": "date-time"},
                    "last_failure_at": {"type": ["string", "null"], "format": "date-time"},
                    "created_at": {"type": ["string", "null"], "format": "date-time"},
                    "updated_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": False,
            },
            "WebhookWithSecret": {
                "allOf": [
                    {"$ref": "#/components/schemas/Webhook"},
                    {
                        "type": "object",
                        "required": ["signing_secret", "secret_shown_once"],
                        "properties": {
                            "signing_secret": {"type": "string", "pattern": "^whsec_"},
                            "secret_shown_once": {"type": "boolean", "const": True},
                        },
                    },
                ]
            },
            "WebhookCreateResponse": {
                "type": "object",
                "required": ["ok", "request_id", "webhook"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "webhook": {"$ref": "#/components/schemas/WebhookWithSecret"},
                },
            },
            "WebhookListResponse": {
                "type": "object",
                "required": ["ok", "request_id", "webhooks"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "webhooks": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Webhook"},
                    },
                },
            },
            "WebhookDelivery": {
                "type": "object",
                "required": ["delivery_id", "webhook_id", "job_id", "event", "status", "attempt_count"],
                "properties": {
                    "delivery_id": {"type": "string"},
                    "webhook_id": {"type": "string"},
                    "job_id": {"type": "string"},
                    "event": {"$ref": "#/components/schemas/WebhookEvent"},
                    "status": {
                        "type": "string",
                        "enum": ["pending", "retrying", "delivering", "succeeded", "failed"],
                    },
                    "attempt_count": {"type": "integer"},
                    "manual_retry_count": {"type": "integer"},
                    "response_status": {"type": ["integer", "null"]},
                    "last_error": {"type": ["string", "null"]},
                    "delivered_at": {"type": ["string", "null"], "format": "date-time"},
                    "next_attempt_at": {"type": ["string", "null"], "format": "date-time"},
                    "created_at": {"type": ["string", "null"], "format": "date-time"},
                    "updated_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": False,
            },
            "WebhookDeliveryAttempt": {
                "type": "object",
                "properties": {
                    "attempt_sequence": {"type": "integer"},
                    "request_timestamp": {"type": "string"},
                    "resolved_ip": {"type": ["string", "null"]},
                    "response_status": {"type": ["integer", "null"]},
                    "success": {"type": "boolean"},
                    "duration_ms": {"type": "integer"},
                    "error": {"type": ["string", "null"]},
                    "response_body_snippet": {
                        "type": ["string", "null"],
                        "maxLength": 2000,
                    },
                    "created_at": {"type": ["string", "null"], "format": "date-time"},
                },
                "additionalProperties": False,
            },
            "WebhookDeliveryListResponse": {
                "type": "object",
                "required": ["ok", "request_id", "deliveries"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "deliveries": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/WebhookDelivery"},
                    },
                },
            },
            "WebhookDeliveryResponse": {
                "type": "object",
                "required": ["ok", "request_id", "delivery"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "delivery": {"$ref": "#/components/schemas/WebhookDelivery"},
                },
            },
            "WebhookDeliveryDetailResponse": {
                "type": "object",
                "required": ["ok", "request_id", "delivery"],
                "properties": {
                    "ok": {"type": "boolean", "const": True},
                    "request_id": {"type": "string"},
                    "delivery": {
                        "allOf": [
                            {"$ref": "#/components/schemas/WebhookDelivery"},
                            {
                                "type": "object",
                                "properties": {
                                    "attempts": {
                                        "type": "array",
                                        "items": {
                                            "$ref": "#/components/schemas/WebhookDeliveryAttempt"
                                        },
                                    }
                                },
                            },
                        ]
                    },
                },
            },
        },
    }
