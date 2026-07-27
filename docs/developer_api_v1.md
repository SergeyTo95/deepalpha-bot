# DeepAlpha Developer API v1

DeepAlpha Developer API lets approved projects authenticate with scoped keys, inspect usage, run asynchronous Quick Analysis jobs, scan Polymarket for analysis candidates, receive signed terminal events, and fund API projects with TON-backed API credit invoices.

## Authentication

Send an API key in the Authorization header:

```http
Authorization: Bearer da_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API keys are shown once when created. The database stores only a SHA-256 hash and a short public prefix.

Key environments:

- `da_test_...` — self-service test environment;
- `da_live_...` — available to an approved project when the global live-key gate is enabled.

Live access is requested and managed in the authenticated Developer Portal. Rotating a key preserves its test/live environment.

## Available endpoints

### Public health

```http
GET /api/v1/health
```

The response includes database status plus Quick Analysis, Opportunity Scan, Signed Webhook, and commercial payment worker/queue health.

### Client account

Requires `account:read`:

```http
GET /api/v1/account
Authorization: Bearer <api-key>
```

The response includes the project credit balance, commercial/live status, monthly spend snapshot, and low-balance state.

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

### OpenAPI and interactive documentation

These endpoints are public:

```http
GET /api/docs
GET /api/openapi.json
GET /api/postman.json
```

- `/api/docs` serves Swagger UI with bearer authorization and Try It Out;
- `/api/openapi.json` is the canonical OpenAPI 3.1 machine contract;
- `/api/postman.json` is an importable Postman Collection v2.1 with variables and request tests.

Contract tests compare registered public v1 routes against the generated specification so undocumented bearer API routes fail CI.

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

## Commercial Developer Portal

Commercial operations use the authenticated DeepAlpha web session rather than bearer API keys:

```http
GET  /app-api/v1/developer/commercial/overview
POST /app-api/v1/developer/projects/{client_id}/credit-invoices
GET  /app-api/v1/developer/projects/{client_id}/credit-invoices
POST /app-api/v1/developer/credit-invoices/{invoice_id}/refresh
POST /app-api/v1/developer/credit-invoices/{invoice_id}/cancel
POST /app-api/v1/developer/projects/{client_id}/live-access/request
POST /app-api/v1/developer/projects/{client_id}/live-keys
POST /app-api/v1/developer/projects/{client_id}/commercial-settings
```

Project owners can:

- purchase configured API credit packages with exact TON invoices;
- inspect invoice status and payment references;
- request administrator-reviewed live access;
- issue `da_live_...` keys after approval;
- configure a monthly credit-spend limit;
- configure a low-balance warning threshold.

No credit package or financial price is invented automatically. Admin Center must explicitly configure packages.

See `docs/api_commercial_launch.md`.

## Planned

```http
POST /api/v1/analyses with mode=deep
Python and TypeScript SDKs generated from OpenAPI 3.1
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
- monthly credit-spend limit;
- low-balance threshold;
- Quick Analysis and Opportunity worker timeouts;
- webhook endpoint and delivery retry limits.

A PostgreSQL reservation trigger enforces the monthly spend limit under concurrency. Current-month reserved and charged units count toward the limit; refunded reservations do not. Over-limit submissions return stable error `monthly_spend_limit_exceeded`.

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
- ledger-backed manual admin adjustments;
- immutable TON credit invoices and exactly-once `purchase` ledger entries.

Default execution products:

| Product | Default credits |
|---|---:|
| `opportunity_scan` | 1 |
| `market_data` | 1 |
| `quick_analysis` | 10 |
| `deep_analysis` | 50 |

Only Opportunity Scan and Quick Analysis are publicly executable at this phase.

See `docs/developer_api_billing.md` and `docs/api_commercial_launch.md`.

## Persistent execution

Quick Analysis, Opportunity Scan, Signed Webhook delivery, and API credit payment reconciliation run in dedicated Supervisor processes backed by PostgreSQL jobs/outbox/invoice rows. They do not depend on in-memory HTTP tasks.

Workers maintain leases or heartbeats, recover safely after restarts, and settle credits idempotently.

## Admin management

Open the `API` section in DeepAlpha Admin Center to:

- create API clients;
- configure daily, monthly, per-minute, and credit limits;
- issue and revoke test/live keys;
- select scopes;
- inspect usage totals;
- edit API execution product prices;
- create and edit API credit purchase packages and explicit TON prices;
- add or remove credits with idempotency protection;
- approve or reject live-access requests;
- inspect invoices, TON references, transaction hashes, and payment errors;
- inspect ledger entries and reservations;
- inspect all API workers, jobs, webhook deliveries, and commercial runtime health.

A raw API key or webhook signing secret is displayed once immediately after creation/rotation and is never shown again.

## Security

- project purchase/live routes require a valid DeepAlpha web session and ownership of the API client;
- portal mutations require `X-DeepAlpha-Portal: 1`;
- API invoice references use `api_pay_...`, isolated from Telegram token payment references;
- invoice settlement validates Treasury snapshot, exact amount, network, timestamp, confirmations, and unique transaction hash;
- credit purchase settlement is atomic and ledger-idempotent;
- all financial and live-key environment gates default off;
- wildcard CORS is removed by the runtime security middleware;
- same-origin requests remain allowed;
- additional origins must be listed in `CORS_ALLOWED_ORIGINS`;
- `Authorization`, `Idempotency-Key`, and request ID headers are explicitly supported;
- the admin secret is exchanged for an HttpOnly, SameSite=Strict admin session cookie and removed from the URL;
- admin and Developer API responses receive `Cache-Control: no-store` and security headers;
- public analysis results omit internal provider names, prompts, and raw agent payloads;
- webhook targets are restricted to public HTTPS port 443 addresses and connections are pinned to freshly validated DNS results;
- webhook signatures use HMAC-SHA256 over the timestamp and raw request body;
- Swagger UI loads a pinned `swagger-ui-dist` version under a restrictive Content Security Policy.

Important environment variables:

```env
CORS_ALLOWED_ORIGINS=https://app.example.com,https://partner.example.com
CORS_ALLOW_LOCALHOST=false
COOKIE_SECURE=true

API_ANALYSIS_WORKER_ENABLED=true
API_OPPORTUNITY_WORKER_ENABLED=true
API_WEBHOOK_WORKER_ENABLED=true
WEBHOOK_SIGNING_MASTER_KEY=<random 32+ character secret>

API_COMMERCIAL_LAUNCH_ENABLED=false
API_LIVE_KEYS_ENABLED=false
API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT=false
API_CREDIT_PACKAGES_JSON=[]
API_CREDIT_INVOICE_TTL_MINUTES=60
API_CREDIT_CONFIRMATION_SECONDS=20
API_COMMERCIAL_WORKER_ENABLED=true
API_COMMERCIAL_POLL_SECONDS=10
TREASURY_INCOMING_ENABLED=false
```
