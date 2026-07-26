# DeepAlpha Quick Analysis API v1

Quick Analysis is the first paid Developer API execution method. It uses the same DeepAlpha analysis pipeline as the Telegram bot, but has independent API authentication, credits, jobs, results, limits, and accounting.

## Requirements

The API key needs both scopes:

```text
analysis:run
analysis:read
```

Only test keys are available through the self-service portal during beta:

```text
da_test_...
```

## Start an analysis

```http
POST /api/v1/analyses
Authorization: Bearer da_test_...
Idempotency-Key: request_01J_example
Content-Type: application/json
```

```json
{
  "market_url": "https://polymarket.com/event/example-market",
  "mode": "quick",
  "language": "en"
}
```

Supported constraints:

- HTTPS Polymarket event or market URL;
- `mode` must be `quick`;
- `language` must be `ru` or `en`;
- JSON body is limited to 16 KiB;
- idempotency key is mandatory.

New job response:

```json
{
  "ok": true,
  "request_id": "req_...",
  "job_id": "job_...",
  "status": "queued",
  "analysis_type": "quick",
  "mode": "quick",
  "idempotent": false,
  "credits_reserved": 10,
  "credit_balance": 90,
  "status_url": "/api/v1/analyses/job_..."
}
```

A newly created job returns HTTP `202`.

Repeating the same idempotency key with the same canonical request returns HTTP `200`, the original job, and no second credit reservation.

Reusing the same idempotency key with a different request returns HTTP `409 idempotency_conflict`.

## Read status and result

```http
GET /api/v1/analyses/{job_id}
Authorization: Bearer da_test_...
```

Queued or running response:

```json
{
  "ok": true,
  "job_id": "job_...",
  "status": "running",
  "analysis_type": "quick",
  "mode": "quick",
  "market_url": "https://polymarket.com/event/example-market",
  "language": "en",
  "progress": 20,
  "credits": {
    "reserved": 10,
    "charged": 0,
    "refunded": 0,
    "reservation_status": "reserved"
  }
}
```

Successful response contains a stable public result:

```json
{
  "ok": true,
  "job_id": "job_...",
  "status": "success",
  "progress": 100,
  "credits": {
    "reserved": 10,
    "charged": 10,
    "refunded": 0,
    "reservation_status": "charged"
  },
  "result": {
    "schema_version": "1.0",
    "analysis_type": "quick",
    "question": "Will ...?",
    "market_url": "https://polymarket.com/event/example-market",
    "market_slug": "example-market",
    "decision": "WATCH",
    "side": "NO",
    "fair_probability": 61.5,
    "market_probability": 55.0,
    "edge_pp": 6.5,
    "confidence": "medium",
    "data_quality_score": 7,
    "independent_probability": true,
    "summary": "...",
    "reasoning": "...",
    "factors": [],
    "risks": [],
    "sources": [],
    "analysis_text": "...",
    "generated_at": "..."
  }
}
```

Internal model names, prompts, raw agent payloads, market internals, and private diagnostics are not part of the public contract.

A client can read only its own jobs. Unknown or foreign job IDs return `404`.

## Credits

The current default Quick Analysis price is 10 API credits. Administrators can change this price or disable the product without a deploy.

Lifecycle:

```text
POST accepted -> credits reserved -> job queued
success       -> reservation charged
internal error -> reservation refunded
```

A charge has a zero ledger delta because credits were removed from the available balance at reservation time.

## Error statuses

| HTTP | Error | Meaning |
|---:|---|---|
| 400 | `missing_idempotency_key` | Request did not include an idempotency key. |
| 400 | `invalid_idempotency_key` | Key format is invalid. |
| 400 | `invalid_market_url` | URL is not an accepted HTTPS Polymarket event/market URL. |
| 400 | `invalid_mode` | Only quick mode is available. |
| 400 | `invalid_language` | Only Russian and English are available. |
| 402 | `insufficient_api_credits` | Project does not have enough available credits. |
| 403 | `insufficient_scope` | Key lacks `analysis:run` or `analysis:read`. |
| 409 | `idempotency_conflict` | The same key was reused for a different request. |
| 409 | `active_job_limit_reached` | Project already has the maximum active jobs. |
| 413 | `request_too_large` | JSON body exceeds 16 KiB. |
| 429 | `rate_limit_exceeded` | Project request limit was exceeded. |
| 503 | `service_unavailable` | Temporary storage or execution infrastructure failure. |

## Persistent worker

`run_api_worker.py` is managed by Supervisor as `api-worker`.

The worker:

1. claims queued jobs with `FOR UPDATE SKIP LOCKED`;
2. marks the job running and creates a lease;
3. runs the existing DeepAlpha analysis pipeline without legacy Telegram/WebApp persistence;
4. normalizes the result into public schema `1.0`;
5. charges or refunds the reservation;
6. recovers expired leases after worker restarts.

Default controls:

```env
API_ANALYSIS_WORKER_ENABLED=true
API_ANALYSIS_WORKER_ALLOW_PREVIEW=false
API_ANALYSIS_MAX_ACTIVE_JOBS_PER_CLIENT=2
API_ANALYSIS_TIMEOUT_SECONDS=120
API_ANALYSIS_MAX_ATTEMPTS=2
API_ANALYSIS_POLL_SECONDS=2
```

The worker stays idle in preview and non-production branches unless explicitly allowed, preventing preview deployments from consuming jobs in a shared database.

## Not included yet

- Opportunity Scan API;
- Deep Analysis mode;
- signed webhooks;
- live self-service keys;
- public wallet operations.
