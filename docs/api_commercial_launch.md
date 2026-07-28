# DeepAlpha API commercial launch

DeepAlpha Developer API commercial launch adds reviewed live access, API-credit packages, durable invoices, exactly-once purchase settlement, daily/monthly spend controls, and low-balance reporting.

The implementation reuses the existing PostgreSQL-backed API billing ledger and the existing TON Treasury transaction verifier. It does not create a second token-payment contour and it never treats a browser assertion as proof of payment.

## Fail-closed runtime gates

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
TREASURY_INCOMING_ENABLED=false
```

`API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT` remains false and is not used to bypass administrator review.

Supported payment providers:

- `ton_treasury` — uses the existing Treasury wallet routing and TON transaction verification;
- `manual` — creates durable invoices and requires an authenticated administrator to mark the invoice paid and credit it.

Manual mode is explicit. It does not claim that an on-chain or card payment was automatically verified.

## Credit packages

Packages are editable in Admin Center → API:

```text
package_code
display_name
credits
price_amount
price_currency
enabled
sort_order
metadata_json
```

The launch supports one explicitly configured currency (`API_CREDIT_CURRENCY`, default `TON`). The server loads the enabled package from PostgreSQL. User input cannot override credits, amount, or currency.

Changing a package never changes an existing invoice because each invoice stores an immutable snapshot.

## Invoice lifecycle

Supported statuses:

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

Normal TON flow:

```text
awaiting_payment → payment_detected → paid → crediting → credited
```

Manual flow:

```text
awaiting_payment → paid (Admin) → crediting → credited (Admin)
```

Invoice records include:

- unpredictable `inv_...` ID;
- owner and API client;
- package code/name snapshot;
- credits, amount, and currency snapshot;
- provider and payment reference;
- payment address or checkout URL when applicable;
- expiry, payment, and credit timestamps;
- non-public provider metadata;
- idempotency key and request fingerprint.

Open invoices per project are bounded by `API_CREDIT_MAX_OPEN_INVOICES`. Creation requires an authenticated Portal session, ownership, an active project, an enabled package, `X-DeepAlpha-Portal: 1`, and an idempotency key or `client_request_id`.

No credits are granted when an invoice is created.

## TON Treasury verification

`ton_treasury` reuses the existing Treasury contour. A transfer is accepted only when:

- transaction execution succeeded;
- destination equals the invoice Treasury snapshot;
- amount exactly equals the invoice snapshot;
- network matches `TON_NETWORK`;
- transaction time is not before invoice creation;
- transaction time is not after invoice expiry;
- confirmation delay elapsed;
- transaction hash is unique;
- comment contains the exact `api_pay_...` reference.

`api_pay_...` remains isolated from Telegram token-purchase references (`pay_...`).

## Exactly-once settlement

Settlement is one PostgreSQL transaction:

1. lock invoice `FOR UPDATE`;
2. lock API client `FOR UPDATE`;
3. reject invalid status;
4. return idempotently when `credited_at` already exists;
5. move invoice to `crediting`;
6. add the invoice snapshot credits to `api_clients.credit_balance`;
7. insert one append-only `api_credit_ledger` event `purchase`;
8. use ledger idempotency key `invoice:<invoice_id>`;
9. move invoice to `credited` and set `credited_at`;
10. append payment/audit events;
11. commit.

Repeated worker scans, repeated Admin credit actions, and process restarts cannot grant credits twice.

`api_payment_events` is append-only and records invoice status transitions, actor, payment reference, metadata, and an idempotency key.

## Live access lifecycle

Project state:

```text
test_only
live_requested
live_approved
live_rejected
live_suspended
```

Portal request:

```http
POST /app-api/v1/developer/projects/{client_id}/live-request
X-DeepAlpha-Portal: 1
Content-Type: application/json
```

```json
{
  "company_name": "Example LTD",
  "website": "https://example.com",
  "use_case": "Prediction market analytics",
  "expected_monthly_requests": 10000,
  "contact": "@username"
}
```

The server validates ownership, active project status, text lengths, monthly volume, and an optional `http`/`https` website. A project cannot have two active `live_requested` applications.

Admin can approve, reject with a reason, suspend, and approve again after correction. Approval never happens merely because an invoice was paid.

## Live keys

A Portal user can issue `da_live_...` only when:

- project is active;
- state is `live_approved`;
- project live flag is enabled;
- global `API_LIVE_KEYS_ENABLED=true`;
- balance meets `API_LIVE_MINIMUM_BALANCE` when non-zero;
- scopes are in the live self-service allowlist;
- active key limit is not exceeded.

Raw keys are shown once. PostgreSQL stores only SHA-256 hash and prefix. Rotation preserves environment: live remains live and test remains test. `wallet:send`, wallet withdrawal, and trading execution scopes remain unavailable.

## Spend controls

Portal endpoint:

```http
PATCH /app-api/v1/developer/projects/{client_id}/billing-controls
X-DeepAlpha-Portal: 1
```

```json
{
  "low_balance_threshold": 20,
  "max_daily_credit_spend": 200,
  "max_monthly_credit_spend": 3000
}
```

A JSON `null` disables a control. Negative values are rejected. `auto_recharge_enabled=true` is rejected until a reusable payment method exists.

A PostgreSQL trigger locks the client before a new reservation. `reserved + charged` units count toward daily/monthly caps; refunded reservations do not. Stable errors:

```text
daily_credit_spend_limit_reached
monthly_credit_spend_limit_reached
```

Idempotent job replay reuses the existing reservation and therefore does not count spend twice.

## Low balance

The Portal and authenticated account response expose:

- current balance;
- threshold and `low_balance`;
- daily/monthly spend and caps;
- estimated remaining Quick Analyses;
- estimated remaining Opportunity Scans;
- durable notification state fields.

No Telegram/email notification is sent by this launch code. Notification state exists so a future approved notifier can apply transition/cooldown logic without spam.

## Portal endpoints

These routes use the authenticated DeepAlpha web session, not bearer-key authentication. Mutations require `X-DeepAlpha-Portal: 1`.

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

Compatibility aliases from the previous commercial beta remain mounted, but the routes above are canonical.

Portal session endpoints are intentionally excluded from the public bearer OpenAPI security scheme.

## Admin actions

Admin session protection is required for:

```http
POST /admin/api/credit-invoices/{invoice_id}/mark-paid
POST /admin/api/credit-invoices/{invoice_id}/credit
POST /admin/api/credit-invoices/{invoice_id}/cancel
POST /admin/api/commercial/live/{client_id}/approve
POST /admin/api/commercial/live/{client_id}/reject
POST /admin/api/commercial/live/{client_id}/suspend
```

Admin UI also shows packages, filtered invoices, payment references, paid/credited timestamps, project spend controls, recent purchase ledger entries, and payment audit events.

## Production worker

Supervisor runs:

```text
commercial-worker -> python run_api_commercial_worker.py
```

The worker is active only for `ton_treasury`, enabled commercial purchases, and the configured production environment/branch. Manual provider mode intentionally keeps the automatic worker idle.

## Still closed

- Deep Analysis execution;
- wallet send and withdrawal;
- trading execution;
- card storage;
- automatic debit;
- auto recharge;
- arbitrary browser-controlled settlement;
- generic provider webhook accepting `paid=true`.

## Production verification

Use `scripts/live_api_commercial_launch_smoke.py` only with a dedicated test project and manual provider/admin credentials. It performs no real payment. A separate controlled on-chain smoke is required before enabling production TON purchases.

GitHub CI validates code and contract behavior. It does not prove Railway deployment, worker startup, Treasury configuration, TON Center availability, or a real on-chain transfer.
