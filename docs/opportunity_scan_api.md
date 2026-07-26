# DeepAlpha Opportunity Scan API v1

Opportunity Scan exposes the existing deterministic Polymarket pre-scanner to Developer API projects. It ranks markets for later analysis without calling Kimi, Gemini, or any other paid AI provider.

It is a triage product, not a trading signal:

- no fair probability;
- no edge calculation;
- no BUY/WATCH decision;
- `provider_calls = 0`;
- `paid_ai_used = false`.

## Billing

Product code:

```text
opportunity_scan
```

Default price:

```text
1 API credit
```

The price remains editable in Admin Center. The request uses the same atomic billing lifecycle as Quick Analysis:

```text
POST accepted -> credit reserved -> durable job queued
success       -> reservation charged
internal error -> reservation refunded
```

Idempotent replay does not create a second reservation.

## Scopes

Starting a scan requires:

```text
opportunities:run
```

Reading a scan requires:

```text
opportunities:read
```

Both are available on self-service test keys. Existing keys must be rotated or replaced with keys containing the new scopes.

## Start a scan

```http
POST /api/v1/opportunity-scans
Authorization: Bearer da_test_...
Idempotency-Key: scan_01J_example
Content-Type: application/json
```

Example:

```json
{
  "category": "All",
  "language": "en",
  "scan_limit": 100,
  "result_limit": 10,
  "min_score": 52,
  "min_liquidity": 1000,
  "min_volume_24h": 500,
  "tiers": [
    "DEEP_ANALYSIS_CANDIDATE",
    "WATCH_CANDIDATE"
  ]
}
```

New job response:

```json
{
  "ok": true,
  "request_id": "req_...",
  "job_id": "job_...",
  "status": "queued",
  "job_type": "opportunity_scan",
  "idempotent": false,
  "credits_reserved": 1,
  "credit_balance": 99,
  "status_url": "/api/v1/opportunity-scans/job_..."
}
```

A new job returns HTTP `202`. Replaying the same idempotency key with the same canonical request returns HTTP `200` and the existing job.

## Request fields

| Field | Default | Limits |
|---|---:|---|
| `category` | `All` | `All`, `Crypto`, `Politics`, `Sports`, `Economy`, `Tech`, `Other` |
| `language` | `en` | `en`, `ru` |
| `scan_limit` | 100 | 10–200 public markets |
| `result_limit` | 10 | 1–20 returned candidates |
| `min_score` | 0 | 0–100 |
| `min_liquidity` | 0 | non-negative public liquidity value |
| `min_volume_24h` | 0 | non-negative public 24-hour volume |
| `tiers` | all tiers | one or more supported tiers |

Supported tiers:

```text
DEEP_ANALYSIS_CANDIDATE
WATCH_CANDIDATE
LOW_PRIORITY
```

Unknown fields are rejected. The API does not expose `force_refresh`; callers cannot bypass the server-side public-data cache on every request.

## Read status and result

```http
GET /api/v1/opportunity-scans/{job_id}
Authorization: Bearer da_test_...
```

Queued/running response:

```json
{
  "ok": true,
  "job_id": "job_...",
  "status": "running",
  "job_type": "opportunity_scan",
  "progress": 30,
  "request": {
    "category": "All",
    "language": "en",
    "scan_limit": 100,
    "result_limit": 10,
    "min_score": 52,
    "min_liquidity": 1000,
    "min_volume_24h": 500,
    "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"]
  },
  "credits": {
    "reserved": 1,
    "charged": 0,
    "refunded": 0,
    "reservation_status": "reserved"
  }
}
```

Successful result:

```json
{
  "status": "success",
  "credits": {
    "reserved": 1,
    "charged": 1,
    "refunded": 0,
    "reservation_status": "charged"
  },
  "result": {
    "schema_version": "1.0",
    "scan_type": "opportunity_scan",
    "provider_calls": 0,
    "paid_ai_used": false,
    "category": "All",
    "language": "en",
    "filters": {},
    "markets_received": 100,
    "eligible_markets": 38,
    "candidate_count": 10,
    "candidates": [
      {
        "market_id": "...",
        "event_key": "...",
        "question": "Will ...?",
        "url": "https://polymarket.com/event/...",
        "category": "Politics",
        "yes_price": 55.4,
        "no_price": 44.6,
        "liquidity": 12500,
        "volume_24h": 4200,
        "volume_total": 85000,
        "hours_to_close": 240,
        "price_move_24h_pp": 2.4,
        "event_market_count": 4,
        "score": 73,
        "tier": "DEEP_ANALYSIS_CANDIDATE",
        "reasons": [],
        "risk_flags": [],
        "score_components": {}
      }
    ],
    "rejection_counts": {},
    "source_cached": true,
    "generated_at": "...",
    "disclaimer": "..."
  }
}
```

A key can read only jobs belonging to its API client.

## Ranking model

The zero-LLM scanner scores active binary Polymarket markets using public metadata:

- liquidity;
- 24-hour volume;
- total volume;
- remaining price-discovery room;
- time to resolution;
- 24-hour price movement;
- event contract structure;
- objective-data accessibility inferred from the question.

It excludes inactive, malformed, nearly resolved, illiquid, too-near, too-distant, and obvious noise/test markets. A diversity cap prevents one event from filling the entire shortlist.

## Dedicated worker

Supervisor runs:

```text
opportunity-worker -> python run_opportunity_worker.py
```

The worker:

1. claims `opportunity_scan` jobs with `FOR UPDATE SKIP LOCKED`;
2. maintains job progress, heartbeat, and lease;
3. runs the deterministic public-data scanner;
4. saves stable schema `1.0`;
5. charges the reservation on success;
6. refunds internal failures;
7. retries stale jobs within the attempt limit;
8. stays idle on preview/non-production branches unless explicitly allowed.

Configuration:

```env
API_OPPORTUNITY_WORKER_ENABLED=true
API_OPPORTUNITY_WORKER_ALLOW_PREVIEW=false
API_OPPORTUNITY_MAX_ACTIVE_JOBS_PER_CLIENT=2
API_OPPORTUNITY_TIMEOUT_SECONDS=45
API_OPPORTUNITY_MAX_ATTEMPTS=2
API_OPPORTUNITY_POLL_SECONDS=1
API_OPPORTUNITY_WORKER_STALE_SECONDS=90
```

## Signed Webhooks

Projects may subscribe to:

```text
opportunity_scan.completed
opportunity_scan.failed
```

The events use the existing HMAC-SHA256 Signed Webhooks transport and do not create another credit charge.

Successful event data includes:

```json
{
  "job_id": "job_...",
  "status": "success",
  "job_type": "opportunity_scan",
  "scan_type": "opportunity_scan",
  "result": {},
  "credits": {
    "reserved": 1,
    "charged": 1,
    "reservation_status": "charged"
  }
}
```

## Monitoring

`GET /api/v1/health` includes `opportunity_scans` with:

- worker availability;
- fresh worker count;
- queued/running/stale jobs;
- pending refunds;
- success/error counts for 24 hours;
- average duration;
- warning codes.

Admin Center → API shows Opportunity workers and recent jobs. The user Developer Portal shows owned Opportunity Scan history and top candidates for each project.

## Production verification

After deployment:

1. confirm `opportunity-worker` starts;
2. confirm `/api/v1/health` reports `opportunity_scans.worker_available=true`;
3. grant a dedicated test project at least 2 credits;
4. create a key with `opportunities:run` and `opportunities:read`;
5. submit one scan and verify reserve 1 → success → charge 1;
6. replay the same idempotency key and verify no second charge;
7. submit a changed payload with the same key and verify `idempotency_conflict`;
8. verify an internal failure returns the reserved credit;
9. subscribe to both Opportunity Scan webhook events and verify the HMAC signature;
10. confirm no LLM/provider usage entry is created by the scan.
