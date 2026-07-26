# DeepAlpha Signed Webhooks v1

Signed Webhooks deliver terminal Quick Analysis results to a project endpoint without polling `GET /api/v1/analyses/{job_id}`.

## Scope

The API key must include:

```text
webhooks:manage
```

The self-service Developer Portal allows this scope on test keys. Wallet permissions remain unavailable.

## Create an endpoint

```http
POST /api/v1/webhooks
Authorization: Bearer da_test_...
Content-Type: application/json
```

```json
{
  "name": "production backend",
  "url": "https://example.com/deepalpha/webhook",
  "events": ["analysis.completed", "analysis.failed"]
}
```

Response:

```json
{
  "ok": true,
  "webhook": {
    "webhook_id": "wh_...",
    "name": "production backend",
    "url": "https://example.com/deepalpha/webhook",
    "events": ["analysis.completed", "analysis.failed"],
    "status": "active",
    "signing_secret": "whsec_...",
    "secret_shown_once": true
  }
}
```

The signing secret is returned only by create and rotate actions. List responses never include it.

The database stores a random salt and SHA-256 integrity hash. The actual secret is derived with a domain-separated server master key and is not stored as plaintext.

Recommended production environment:

```env
WEBHOOK_SIGNING_MASTER_KEY=<at least 32 random characters>
```

For compatibility, the service can derive a separate webhook master from `ADMIN_SECRET_KEY`, but a dedicated key is preferred.

## Events

Current event types:

```text
analysis.completed
analysis.failed
```

Completed payload:

```json
{
  "event": "analysis.completed",
  "delivery_id": "delivery_...",
  "created_at": "2026-07-26T16:00:00.000Z",
  "data": {
    "job_id": "job_...",
    "status": "success",
    "analysis_type": "quick",
    "result": {
      "schema_version": "1.0",
      "decision": "WATCH"
    },
    "error": null,
    "credits": {
      "reserved": 10,
      "charged": 10,
      "reservation_status": "charged"
    }
  }
}
```

Failed payload contains the stable API error and refunded reservation state:

```json
{
  "event": "analysis.failed",
  "delivery_id": "delivery_...",
  "data": {
    "job_id": "job_...",
    "status": "error",
    "result": null,
    "error": "analysis_failed",
    "credits": {
      "reserved": 10,
      "charged": 0,
      "reservation_status": "refunded"
    }
  }
}
```

The PostgreSQL trigger creates delivery outbox rows in the same transaction that changes the job to `success` or `error`. Webhook delivery does not reserve or charge credits again.

## Signature

Every request contains:

```http
X-DeepAlpha-Event: analysis.completed
X-DeepAlpha-Delivery: delivery_...
X-DeepAlpha-Timestamp: 1785081600
X-DeepAlpha-Signature: v1=<hex digest>
```

Signing input:

```text
timestamp + "." + raw_request_body
```

Algorithm:

```text
HMAC-SHA256(signing_secret, signing_input)
```

Python verification:

```python
import hashlib
import hmac
import time


def verify(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    if abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "v1=" + hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Verify the signature against the exact raw bytes before parsing JSON. Reject timestamps outside a short tolerance such as five minutes, and deduplicate by `X-DeepAlpha-Delivery`.

## Management routes

```http
GET    /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
POST   /api/v1/webhooks/{webhook_id}/rotate-secret
```

Disabling an endpoint prevents new claims and marks its queued deliveries failed. Historical delivery attempts remain available.

Rotating returns a new secret once. Requests signed after rotation immediately use the new secret.

## Delivery journal

```http
GET /api/v1/webhook-deliveries?limit=50
GET /api/v1/webhook-deliveries/{delivery_id}
POST /api/v1/webhook-deliveries/{delivery_id}/retry
```

The detail response includes an attempt journal with:

- sequence number;
- request timestamp;
- resolved public IP;
- HTTP response status;
- success flag;
- duration;
- stable transport/error code;
- a response-body snippet capped at 2,000 characters.

Raw signing secrets and full remote response bodies are never returned.

## Retry policy

Default automatic schedule after failed attempts:

```text
30 seconds
2 minutes
10 minutes
30 minutes
2 hours
```

The default maximum is six attempts. Manual retry resets the automatic attempt cycle while preserving the historical attempt journal.

Endpoints are automatically disabled after 20 consecutive failed delivery attempts by default.

Configuration:

```env
API_WEBHOOK_WORKER_ENABLED=true
API_WEBHOOK_WORKER_ALLOW_PREVIEW=false
API_WEBHOOK_MAX_PER_CLIENT=5
API_WEBHOOK_TIMEOUT_SECONDS=10
API_WEBHOOK_MAX_ATTEMPTS=6
API_WEBHOOK_DISABLE_AFTER_FAILURES=20
API_WEBHOOK_POLL_SECONDS=1
API_WEBHOOK_WORKER_STALE_SECONDS=90
API_WEBHOOK_QUEUE_WARNING_SIZE=50
API_WEBHOOK_QUEUE_WARNING_AGE_SECONDS=300
```

## SSRF and network controls

Endpoint registration and every delivery attempt enforce:

- HTTPS only;
- port 443 only;
- no URL credentials;
- no localhost or `.local` names;
- no private, loopback, link-local, multicast, reserved, or otherwise non-global IPs;
- fresh DNS resolution for every attempt;
- rejection when any returned address is non-public;
- connection pinned to the validated resolved IP;
- TLS certificate verification with SNI for the original hostname;
- no redirect following;
- request timeout and response-body cap.

Resolving again and pinning the connection prevents a DNS-rebinding change between validation and connection.

## Worker and health

Supervisor runs a separate process:

```text
webhook-worker -> python run_webhook_worker.py
```

It claims rows with `FOR UPDATE SKIP LOCKED`, maintains a delivery lease, records a heartbeat, retries failures, and recovers expired `delivering` leases after a restart.

`GET /api/v1/health` includes a `webhooks` block with worker availability, active endpoint count, queue size, recent success/failure counts, and stable warning codes.

Admin Center → API shows webhook workers, endpoints, deliveries, response statuses, failure counts, and errors.

## Production verification

After deployment:

1. set `WEBHOOK_SIGNING_MASTER_KEY`;
2. confirm the `webhook-worker` process starts;
3. create a public HTTPS test receiver;
4. save the one-time `whsec_...` value;
5. run one successful Quick Analysis;
6. verify one `analysis.completed` delivery and valid signature;
7. run one terminal-error analysis and verify `analysis.failed`;
8. return HTTP 500 and verify retries plus the attempt journal;
9. use manual retry and verify the same delivery ID is redelivered;
10. confirm credits contain only the analysis reserve plus charge/refund, with no webhook billing entry.
