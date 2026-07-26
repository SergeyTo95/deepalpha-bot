# DeepAlpha Developer API v1

This foundation exposes account and usage metadata for approved API clients. Analysis execution, opportunity scanning, webhooks, and wallet operations are intentionally not public yet.

## Authentication

Send an API key in the Authorization header:

```http
Authorization: Bearer da_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

API keys are shown once when created. The database stores only a SHA-256 hash and a short public prefix.

Key environments:

- `da_test_...`
- `da_live_...`

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

The capabilities response clearly separates currently available routes from planned routes.

## Planned but disabled

These routes are not registered in this phase:

```http
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
GET  /api/v1/opportunities
```

The API credit ledger, configurable products, atomic reservations, job idempotency, success charging, and failure refunds are now prepared. Public analysis execution still waits for the worker, stable result contract, concurrency controls, timeouts, and signed webhook delivery.

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
- API credit balance.

Every authenticated request is recorded with request ID, endpoint, method, status, units, latency, client ID, and key ID.

## Billing

Developer API credits are separate from Telegram user tokens.

The billing foundation provides:

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

## Admin management

Open the `API` section in DeepAlpha Admin Center to:

- create API clients;
- configure daily, monthly, and per-minute limits;
- set initial API credits;
- issue test or live keys;
- select scopes;
- inspect usage totals;
- revoke keys;
- edit API product prices and enabled status;
- add or remove credits with idempotency protection;
- inspect recent ledger entries and reservations.

A raw key is displayed once immediately after creation and is never shown again.

## Security changes

- `/api/user/{user_id}` requires a valid DeepAlpha web session and only permits access to the authenticated user's own ID;
- wildcard CORS is removed by the runtime security middleware;
- same-origin requests remain allowed;
- additional origins must be listed in `CORS_ALLOWED_ORIGINS`;
- the admin secret is exchanged for an HttpOnly, SameSite=Strict admin session cookie and removed from the URL;
- admin and Developer API responses receive `Cache-Control: no-store` and security headers.

Optional environment variables:

```env
CORS_ALLOWED_ORIGINS=https://app.example.com,https://partner.example.com
CORS_ALLOW_LOCALHOST=false
COOKIE_SECURE=true
```
