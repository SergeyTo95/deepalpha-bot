# DeepAlpha Developer API v1

DeepAlpha Developer API lets approved projects authenticate with scoped keys, inspect usage, and run asynchronous Quick Analysis jobs against Polymarket markets.

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

The POST request atomically reserves credits and returns a durable `job_id`.

Requires `analysis:read`:

```http
GET /api/v1/analyses/{job_id}
Authorization: Bearer <api-key>
```

The result endpoint returns queued, running, success, or error state. A key can read only jobs owned by its API client.

See `docs/quick_analysis_api.md` for request schemas, result fields, credits, errors, worker recovery, and examples.

## Planned

```http
GET /api/v1/opportunities
POST /api/v1/webhooks
POST /api/v1/analyses with mode=deep
```

Wallet send and withdrawal operations are not planned for the public Developer API.

## Scopes

Recognized scopes:

- `account:read`
- `usage:read`
- `analysis:run`
- `analysis:read`
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
- active Quick Analysis jobs.

Default active Quick Analysis limit is two queued/running jobs per client.

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

See `docs/developer_api_billing.md` for the complete lifecycle.

## Persistent execution

Quick Analysis runs in a dedicated Supervisor process backed by PostgreSQL jobs. It does not depend on an in-memory HTTP task.

The worker claims jobs with `FOR UPDATE SKIP LOCKED`, maintains a lease, retries stale work within the configured attempt limit, and refunds credits when execution cannot complete.

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
- inspect recent ledger entries and reservations.

A raw key is displayed once immediately after creation and is never shown again.

## Security

- `/api/user/{user_id}` requires a valid DeepAlpha web session and only permits access to the authenticated user's own ID;
- wildcard CORS is removed by the runtime security middleware;
- same-origin requests remain allowed;
- additional origins must be listed in `CORS_ALLOWED_ORIGINS`;
- `Authorization`, `Idempotency-Key`, and request ID headers are explicitly supported;
- the admin secret is exchanged for an HttpOnly, SameSite=Strict admin session cookie and removed from the URL;
- admin and Developer API responses receive `Cache-Control: no-store` and security headers;
- public analysis results omit internal provider names, prompts, and raw agent payloads.

Optional environment variables:

```env
CORS_ALLOWED_ORIGINS=https://app.example.com,https://partner.example.com
CORS_ALLOW_LOCALHOST=false
COOKIE_SECURE=true
API_ANALYSIS_WORKER_ENABLED=true
API_ANALYSIS_MAX_ACTIVE_JOBS_PER_CLIENT=2
API_ANALYSIS_TIMEOUT_SECONDS=120
API_ANALYSIS_MAX_ATTEMPTS=2
```
