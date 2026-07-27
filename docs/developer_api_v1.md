# DeepAlpha Developer API v1

DeepAlpha Developer API provides scoped bearer access to durable Quick Analysis jobs, deterministic Opportunity Scan jobs, usage/billing data, and HMAC-signed terminal webhooks. Commercial activation, credit purchases, invoices, and live-key issuance are managed through the authenticated Developer Portal.

## Authentication

```http
Authorization: Bearer da_test_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Key environments:

- `da_test_...` — self-service test key;
- `da_live_...` — production key available only after administrator approval and while live issuance is globally enabled.

A raw key is displayed once. PostgreSQL stores only its SHA-256 hash and public prefix. Rotation preserves environment: test remains test and live remains live.

## Public Developer API

### Health and documentation

```http
GET /api/v1/health
GET /api/docs
GET /api/openapi.json
GET /api/postman.json
```

`/api/openapi.json` is the runtime OpenAPI 3.1 contract. `docs/openapi.json` is the committed public-route snapshot validated in CI. Portal session endpoints are deliberately excluded from the bearer security scheme.

### Account, usage, and capabilities

```http
GET /api/v1/account       # account:read
GET /api/v1/usage         # usage:read
GET /api/v1/capabilities  # account:read
```

The account response includes credit balance, live state, provider mode, daily/monthly spend controls, low-balance state, and estimated remaining analyses.

### Quick Analysis

```http
POST /api/v1/analyses             # analysis:run
GET  /api/v1/analyses/{job_id}    # analysis:read
```

Example:

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

Quick Analysis costs 10 API credits by default. Deep Analysis execution remains closed.

### Opportunity Scan

```http
POST /api/v1/opportunity-scans            # opportunities:run
GET  /api/v1/opportunity-scans/{job_id}   # opportunities:read
```

Opportunity Scan costs 1 API credit by default, is deterministic, uses zero paid LLM calls, and does not return a fair probability or trading decision.

### Signed Webhooks

```http
POST   /api/v1/webhooks
GET    /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
POST   /api/v1/webhooks/{webhook_id}/rotate-secret
GET    /api/v1/webhook-deliveries
GET    /api/v1/webhook-deliveries/{delivery_id}
POST   /api/v1/webhook-deliveries/{delivery_id}/retry
```

Scope: `webhooks:manage`.

Events:

```text
analysis.completed
analysis.failed
opportunity_scan.completed
opportunity_scan.failed
```

## Scopes

Recognized public scopes:

- `account:read`
- `usage:read`
- `analysis:run`
- `analysis:read`
- `opportunities:run`
- `opportunities:read`
- `markets:read`
- `webhooks:manage`

`wallet:send`, wallet withdrawal, and trading execution are not available Developer API scopes.

## Billing model

API credits are separate from Telegram user tokens.

The billing system uses:

- editable `api_products` prices;
- append-only `api_credit_ledger`;
- atomic `api_credit_reservations`;
- reserve → charge/refund lifecycle;
- canonical request fingerprints;
- `(client_id, idempotency_key)` uniqueness;
- PostgreSQL row locks and durable jobs.

Default products:

| Product | Default credits | Public execution |
|---|---:|---|
| `opportunity_scan` | 1 | yes |
| `market_data` | 1 | internal/supporting |
| `quick_analysis` | 10 | yes |
| `deep_analysis` | 50 | no |

## Commercial Developer Portal

Canonical routes:

```http
GET   /app-api/v1/developer/commercial/overview
POST  /app-api/v1/developer/projects/{client_id}/live-request
POST  /app-api/v1/developer/projects/{client_id}/live-keys
POST  /app-api/v1/developer/projects/{client_id}/credit-invoices
GET   /app-api/v1/developer/projects/{client_id}/credit-invoices
GET   /app-api/v1/developer/credit-invoices/{invoice_id}
POST  /app-api/v1/developer/credit-invoices/{invoice_id}/refresh
POST  /app-api/v1/developer/credit-invoices/{invoice_id}/cancel
PATCH /app-api/v1/developer/projects/{client_id}/billing-controls
```

These endpoints require an authenticated DeepAlpha web session. Mutations require:

```http
X-DeepAlpha-Portal: 1
```

Ownership is checked server-side for every project, live-key, and invoice operation.

### Live activation

Lifecycle:

```text
test_only
live_requested
live_approved
live_rejected
live_suspended
```

Request body:

```json
{
  "company_name": "Example LTD",
  "website": "https://example.com",
  "use_case": "Prediction market analytics",
  "expected_monthly_requests": 10000,
  "contact": "@username"
}
```

Only `http` and `https` websites are accepted. There is no automatic approval on payment. Admin can approve, reject with a reason, suspend, and approve again after corrections.

A live key additionally requires:

- active project;
- `live_approved` state;
- `API_LIVE_KEYS_ENABLED=true`;
- minimum balance when configured;
- allowed scopes;
- available key slot.

### Credit packages and invoices

Packages contain server-controlled credits, amount, currency, enabled state, sort order, and metadata. User requests send only `package_code`; the server snapshots the package into the invoice.

Invoice statuses:

```text
pending
awaiting_payment
payment_detected
paid
crediting
credited
expired
cancelled
failed
refunded
```

One invoice can increase balance at most once. Settlement locks invoice and client, uses ledger idempotency key `invoice:<invoice_id>`, writes one `purchase` entry, sets `credited_at`, appends audit/payment events, and commits atomically.

Payment providers:

- `ton_treasury` — real Treasury routing plus exact TON transaction validation;
- `manual` — explicit authenticated Admin settlement with no fake automatic verification.

The launch does not accept a public callback such as `{ "invoice_id": "...", "paid": true }`.

### Spend controls

```json
{
  "low_balance_threshold": 20,
  "max_daily_credit_spend": 200,
  "max_monthly_credit_spend": 3000
}
```

`null` disables a limit. Reserved and charged units count until refund. Stable errors:

```text
daily_credit_spend_limit_reached
monthly_credit_spend_limit_reached
```

Idempotent replay does not create a second reservation and therefore does not count spend twice.

Auto recharge remains disabled until a secure reusable payment method exists.

### Low balance

Overview returns balance, threshold, `low_balance`, daily/monthly spend, caps, estimated remaining Quick Analyses, and estimated remaining Opportunity Scans. Durable notification state is stored, but this launch sends no Telegram/email notification by itself.

See `docs/api_commercial_launch.md`.

## Admin management

Admin Center → API supports:

- package create/edit/enable/disable;
- live request approve/reject/suspend/re-approve;
- invoice filters by status/client/provider;
- payment references, amount/currency, paid/credited timestamps;
- manual `mark-paid`, exactly-once `credit`, and cancel actions;
- project spend controls;
- purchase ledger and payment audit trail;
- TON scanner and commercial worker health.

Admin mutations use the existing authenticated Admin session. Paid/credited invoices and append-only ledger/payment events are not deleted or edited.

## Security invariants

- no raw API-key hash or provider secret in Portal responses;
- raw API key appears once;
- Portal ownership and mutation-header checks;
- Admin session for approval and settlement;
- unpredictable invoice IDs and references;
- amount/credits/currency snapshots;
- PostgreSQL `FOR UPDATE` locks;
- append-only ledger and payment events;
- unique transaction hash for TON settlement;
- no card storage;
- no withdrawal or trading execution;
- all commercial gates default closed;
- financial responses use `Cache-Control: no-store` through the existing security middleware.

## Railway configuration

```env
API_COMMERCIAL_LAUNCH_ENABLED=false
API_LIVE_KEYS_ENABLED=false
API_LIVE_MINIMUM_BALANCE=10
API_CREDIT_PURCHASES_ENABLED=false
API_CREDIT_INVOICE_PROVIDER=ton_treasury
API_CREDIT_CURRENCY=TON
API_CREDIT_INVOICE_TTL_HOURS=24
API_CREDIT_MAX_OPEN_INVOICES=3
API_CREDIT_CONFIRMATION_SECONDS=20
API_LOW_BALANCE_NOTIFICATION_COOLDOWN_HOURS=24
API_COMMERCIAL_WORKER_ENABLED=true
API_COMMERCIAL_POLL_SECONDS=10
TREASURY_INCOMING_ENABLED=false
```

For `manual`, also configure `API_CREDIT_PAYMENT_ADDRESS` and `API_CREDIT_MANUAL_PAYMENT_INSTRUCTIONS` as applicable. Manual mode still requires Admin settlement.

GitHub CI does not prove Railway deployment, process heartbeat, Treasury configuration, TON Center availability, or real on-chain settlement.
