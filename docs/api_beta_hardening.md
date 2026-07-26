# DeepAlpha API Beta Hardening

This phase adds operational visibility around the billed Quick Analysis API before more public methods are opened.

## Runtime health

`GET /api/v1/health` now reports the state of PostgreSQL-backed execution:

```json
{
  "ok": true,
  "service": "deepalpha-developer-api",
  "version": "v1",
  "status": "operational",
  "database": {"available": true},
  "worker": {
    "available": true,
    "fresh_workers": 1,
    "stale_after_seconds": 180
  },
  "queue": {
    "queued": 0,
    "running": 0,
    "refund_pending": 0,
    "stale_running": 0,
    "oldest_queued_age_seconds": 0
  },
  "recent": {
    "success_24h": 4,
    "error_24h": 1,
    "avg_duration_seconds_24h": 38.2
  },
  "warnings": []
}
```

The endpoint is public, contains no secrets, and caches the database result for five seconds to avoid turning health polling into unnecessary database load.

HTTP behavior:

- `200 operational` — worker and queue look healthy;
- `200 degraded` — API is reachable but the worker, queue, lease, or refund state needs attention;
- `503 unavailable` — runtime health could not be read from storage.

Possible warnings:

- `no_fresh_api_worker`;
- `api_queue_size_high`;
- `api_queue_wait_high`;
- `stale_running_jobs`;
- `refunds_pending`.

Configurable thresholds:

```env
API_WORKER_HEARTBEAT_SECONDS=10
API_WORKER_STALE_SECONDS=180
API_QUEUE_WARNING_SIZE=10
API_QUEUE_WARNING_AGE_SECONDS=180
```

## Worker heartbeat

The persistent `api-worker` writes durable heartbeats to `api_worker_heartbeats`.

States include:

```text
starting
idle
running
­degraded
stopped
```

While an analysis is running, a daemon heartbeat thread continues updating the worker row and current job ID. A process restart creates a new worker identity while old rows naturally become stale.

Heartbeat failures are logged but do not crash the analysis loop. Existing job leases and stale-job recovery remain the billing safety mechanism.

## Admin jobs dashboard

The existing Admin Center API section now includes:

- runtime status;
- fresh worker count;
- queued and running jobs;
- stale leases and pending refunds;
- oldest queue age;
- success/error counts for the last 24 hours;
- average execution duration;
- worker heartbeat table;
- recent Quick Analysis jobs;
- filtering by status and API client ID;
- reserved versus charged credits;
- reservation state, attempts, duration, decision, and stable error code.

The dashboard updates the old billing-foundation copy to reflect that Quick Analysis execution is active.

## User job history

The Developer Portal adds an owned project history route:

```http
GET /app-api/v1/developer/projects/{client_id}/jobs?limit=30
Cookie: deepalpha_session=...
```

Every query joins through `api_client_owners`. A user cannot read another project's jobs; foreign project IDs return `404 project_not_found`.

The response contains only normalized fields used by the portal:

- job ID;
- status and progress;
- market URL and language;
- decision and side;
- stable error code;
- reserved/charged credits and reservation status;
- attempts and duration;
- created, started, finished, and updated timestamps.

Raw request payloads, raw result JSON, API-key hashes, secrets, prompts, and provider diagnostics are not returned.

The portal shows a history table inside each project card and polls every ten seconds only while queued or running jobs are visible.

## Live billing smoke test

Use a dedicated test API project so concurrent requests cannot affect balance assertions.

Success path:

```bash
python scripts/quick_analysis_api_smoke.py \
  --base-url https://YOUR-DEEPALPHA-HOST \
  --api-key da_test_... \
  --market-url https://polymarket.com/event/REAL-MARKET-SLUG \
  --language en \
  --expect success \
  --strict-balance
```

The script verifies:

```text
balance before
→ POST reserves credits
→ queued/running polling
→ success
→ reserved == charged
→ no refund
→ final balance = initial balance - charged credits
```

Refund path can be tested with a syntactically valid Polymarket URL that cannot be resolved by the analysis pipeline:

```bash
python scripts/quick_analysis_api_smoke.py \
  --base-url https://YOUR-DEEPALPHA-HOST \
  --api-key da_test_... \
  --market-url https://polymarket.com/event/nonexistent-smoke-market \
  --language en \
  --expect error \
  --strict-balance
```

The script then verifies:

```text
credits reserved
→ terminal error
→ reserved == refunded
→ reservation_status == refunded
→ final balance = initial balance
```

Do not run a failure smoke against a real active market because a successful analysis is correctly charged.

## Deployment verification

After deployment:

1. open `/api/v1/health` and confirm `database.available=true`;
2. confirm at least one fresh worker heartbeat;
3. verify the queue is not growing;
4. open Admin Center → API and inspect the worker/job sections;
5. run the success smoke with a dedicated test project;
6. run the refund smoke;
7. confirm the same jobs appear in the user's Developer Portal history;
8. confirm the credit ledger contains one reserve plus either one charge or one refund.

GitHub CI validates code structure and settlement rules, but it cannot prove that Railway has deployed the latest branch or that the production worker is connected to the intended PostgreSQL database.
