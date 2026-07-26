# DeepAlpha Developer API v1

DeepAlpha Developer API lets approved projects authenticate with scoped keys, inspect usage, run asynchronous Quick Analysis jobs, scan Polymarket for analysis candidates, and receive signed terminal events.

## Authentication

Send an API key in the Authorization header:

```http
Authorization: Bearer da_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API keys are shown once when created. The database stores only a SHA-256 hash and a short public prefix.

Key environments:

- `da_test_...` — available through the user Developer Portal during beta;
- `da_live_...` — administrator-controlled until public launch.

## Available endpoints

### Public health

```http
GET /api/v1/health
```

The response includes database status plus Quick Analysis, Opportunity Scan, and Signed Webhook worker/queue health.

### Client account

Requires `account:read`:

```http
GET /api/v1/account
Authorization: Bearer <api-key>
```

### Usage

Requires `usage:read`:

```http
GET /api/v1/usage
Authorization: Bearer <api-key>
```

### Capabilities

Requires `account:read`:

```http
GET /api/v1/capabilities
Authorization: Bearer <api-key>
```

### Quick Analysis

Requires `analysis:run`:

```http
POST /api/v1/analyses
Authorization: Bearer <api-key>
Idempotency-Key: request_01J...
Content-Type: application/json
```

```json
{
  "market_url": "https://polymarket.com/event/example-market",
  "mode": "quick",
  "language": "en"
}
```

Read with `analysis:read`:

```http
GET /api/v1/analyses/{job_id}
Authorization: Bearer <api-key>
```

See `docs/quick_analysis_api.md`.

### Opportunity Scan

Starting a scan requires `opportunities:run`:

```http
POST /api/v1/opportunity-scans
Authorization: Bearer <api-key>
Idempotency-Key: scan_01J...
Content-Type: application/json
```

```json
{
  "category": "All",
  "language": "en",
  "scan_limit": 100,
  "result_limit": 10,
  "min_score": 52,
  "min_liquidity": 1000,
  "min_volume_24h": 500,
  "tiers": ["DEEP_ANALYSIS_CANDIDATE", "WATCH_CANDIDATE"]
}
```

Read with `opportunities:read`:

```http
GET /api/v1/opportunity-scans/{job_id}
Authorization: Bearer <api-key>
```

Opportunity Scan costs 1 API credit by default and uses zero paid AI-provider calls. It ranks markets for later analysis and does not produce fair probability, edge, or a BUY decision.

See `docs/opportunity_scan_api.md`.

### Signed Webhooks

Requires `webhooks:manage`:

```http
POST   /api/v1/webhooks
GET    /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
POST   /api/v1/webhooks/{webhook_id}/rotate-secret
GET    /api/v1/webhook-deliveries
GET    /api/v1/webhook-deliveries/{delivery_id}
POST   /api/v1/webhook-deliveries/{delivery_id}/retry
```

Current events:

```text
analysis.completed
analysis.failed
opportunity_scan.completed
opportunity_scan.failed
```

See `docs/signed_webhooks_v1.md`.

## Planned

```http
POST /api/v1/analyses with mode=deep
OpenAPI 3.1 / Swagger
Python and TypeScript SDKs
```

Wallet send and withdrawal operations are not planned for the public Developer API.

## Scopes

Recognized scopes:

- `account:read`
- `usage:read`
- `analysis:run`
- `analysis:read`
- `opportunities:run`
- `opportunities:read`
- `markets:read`
- `webhooks:manage`

A key receives only explicitly selected recognized scopes. `wallet:send` is not a valid Developer API permission.

## Limits

Each client has independent controls:

- requests per minute;
- requests per day;
- requests per month;
- available API credit balance;
- active queued/running API jobs;
- Quick Analysis and Opportunity worker timeouts;
- webhook endpoint and delivery retry limits.

The default active-job limit is two queued/running jobs per project. Quick Analysis and Opportunity Scan submissions share the same project-level serialization lock so concurrent requests cannot bypass the limit.

Every authenticated request is recorded with request ID, endpoint, method, status, units, latency, client ID, and key ID.

## Billing

Developer API credits are separate from Telegram user tokens.

The billing system provides:

- editable prices in `api_products`;
- append-only `api_credit_ledger`;
- atomic `api_credit_reservations`;
- canonical request fingerprints;
- `(client_id, idempotency_key)` uniqueness;
- reserve on job creation;
- charge finalization on success;
- automatic refund on internal failure;
- ledger-backed manual admin adjustments.

Default products:

| Product | Default credits |
|---|---:|
| `opportunity_scan` | 1 |
| `market_data` | 1 |
| `quick_analysis` | 10 |
| `deep_analysis` | 50 |

Only Opportunity Scan and Quick Analysis are publicly executable at this phase.

See `docs/developer_api_billing.md`.

## Persistent execution

Quick Analysis, Opportunity Scan, and Signed Webhook delivery run in dedicated Supervisor processes backed by PostgreSQL jobs/outbox rows. They do not depend on in-memory HTTP tasks.

Workers claim work with `FOR UPDATE SKIP LOCKED`, maintain leases and heartbeats, recover stale work after restarts, and settle credits idempotently.

## Admin management

Open the `API` section in DeepAlpha Admin Center to:

- create API clients;
- configure daily, monthly, per-minute, and credit limits;
- issue test or live keys;
- select scopes;
- inspect usage totals;
- revoke keys;
- edit API product prices and enabled status;
- add or remove credits with idempotency protection;
- inspect ledger entries and reservations;
- inspect Quick Analysis and Opportunity Scan workers/jobs;
- inspect webhook endpoints, workers, deliveries, retries, HTTP statuses, and errors.

A raw API key or webhook signing secret is displayed once immediately after creation/rotation and is never shown again.

## Security

- `/api/user/{user_id}` requires a valid DeepAlpha web session and only permits access to the authenticated user's own ID;
- wildcard CORS is removed by the runtime security middleware;
- same-origin requests remain allowed;
- additional origins must be listed in `CORS_ALLOWED_ORIGINS`;
- `Authorization`, `Idempotency-Key`, and request ID headers are explicitly supported;
- the admin secret is exchanged for an HttpOnly, SameSite=Strict admin session cookie and removed from the URL;
- admin and Developer API responses receive `Cache-Control: no-store` and security headers;
- public analysis results omit internal provider names, prompts, and raw agent payloads;
- webhook targets are restricted to public HTTPS port 443 addresses and connections are pinned to freshly validated DNS results;
- webhook signatures use HMAC-SHA256 over the timestamp and raw request body.

Optional environment variables:

```env
CORS_ALLOWED_ORIGINS=https://app.example.com,https://partner.example.com
CORS_ALLOW_LOCALHOST=false
COOKIE_SECURE=true

API_ANALYSIS_WORKER_ENABLED=true
API_ANALYSIS_MAX_ACTIVE_JOBS_PER_CLIENT=2
API_ANALYSIS_TIMEOUT_SECONDS=120
API_ANALYSIS_MAX_ATTEMPTS=2

API_OPPORTUNITY_WORKER_ENABLED=true
API_OPPORTUNITY_MAX_ACTIVE_JOBS_PER_CLIENT=2
API_OPPORTUNITY_TIMEOUT_SECONDS=45
API_OPPORTUNITY_MAX_ATTEMPTS=2

API_WEBHOOK_WORKER_ENABLED=true
WEBHOOK_SIGNING_MASTER_KEY=<random 32+ character secret>
```
