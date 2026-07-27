# DeepAlpha API commercial launch

This module turns the completed Developer API beta into a controlled commercial product:

- users buy API credits through immutable TON invoices;
- paid credits enter the existing append-only API ledger;
- projects request live access;
- administrators approve or reject live projects;
- approved projects issue `da_live_...` keys from the Developer Portal;
- project owners set monthly credit-spend limits and low-balance thresholds.

All financial and live-key features fail closed by default.

## Runtime gates

```env
API_COMMERCIAL_LAUNCH_ENABLED=false
API_LIVE_KEYS_ENABLED=false
API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT=false
TREASURY_INCOMING_ENABLED=false
```

Recommended rollout:

1. configure and verify the single active Treasury wallet;
2. configure TON Center for the selected `TON_NETWORK`;
3. create credit packages in Admin Center → API;
4. enable `TREASURY_INCOMING_ENABLED=true`;
5. enable `API_COMMERCIAL_LAUNCH_ENABLED=true`;
6. keep `API_LIVE_KEYS_ENABLED=false` while testing purchases;
7. run a real on-chain invoice smoke test;
8. enable `API_LIVE_KEYS_ENABLED=true` when live projects may issue keys.

`API_LIVE_ACCESS_AUTO_APPROVE_ON_PAYMENT` should normally remain false. When false, paying an invoice adds credits but does not bypass administrator review.

## Credit packages

No financial price is invented or enabled automatically.

Packages are created in Admin Center → API with:

- stable package code;
- display name;
- API credit quantity;
- exact TON price;
- enabled flag;
- sort order.

An optional deployment seed may be supplied:

```env
API_CREDIT_PACKAGES_JSON=[{"package_code":"starter_100","display_name":"Starter 100","credits":100,"price_nano":500000000,"enabled":true,"sort_order":10}]
```

The seed uses `ON CONFLICT DO NOTHING`; it does not overwrite later administrator pricing.

## Invoice flow

The authenticated Developer Portal creates an `api_credit_invoices` row containing immutable snapshots of:

- owner and API client;
- package code and name;
- credits to grant;
- exact TON amount in nanoTON;
- Treasury wallet ID and address;
- TON network;
- expiry;
- idempotency key and canonical request fingerprint.

Invoice references use a dedicated prefix:

```text
api_pay_<random>
```

The existing Telegram token-purchase scanner only processes `pay_...` references. `api_pay_...` therefore cannot accidentally grant Telegram user tokens.

The portal returns:

- Treasury address;
- exact nanoTON and TON amounts;
- mandatory text comment;
- TON transfer URI;
- BoC comment payload;
- expiry and invoice status.

A transfer is accepted only when:

- the transaction completed successfully;
- destination equals the invoice Treasury snapshot;
- amount exactly equals the invoice amount;
- invoice network equals runtime `TON_NETWORK`;
- transaction timestamp is not before invoice creation;
- transaction timestamp is not after invoice expiry;
- the configured confirmation delay elapsed;
- transaction hash has not funded another invoice.

An expired invoice can still settle when the transaction was sent before its expiry but was discovered later.

## Atomic credit settlement

The commercial worker locks the invoice and API client in PostgreSQL.

Exactly once it:

1. adds package credits to `api_clients.credit_balance`;
2. inserts an append-only `api_credit_ledger` entry with event `purchase`;
3. uses ledger idempotency key `invoice:<invoice_id>`;
4. stores the unique TON transaction hash;
5. marks the invoice `paid`.

A worker restart or repeated chain scan returns the existing settlement and never grants the credits twice.

The payment worker does not advance or depend on the generic Treasury transaction cursor. It performs a bounded, independently locked Treasury scan so it cannot skip or reorder other product payments.

## Worker

Supervisor runs:

```text
commercial-worker -> python run_api_commercial_worker.py
```

Configuration:

```env
API_COMMERCIAL_WORKER_ENABLED=true
API_COMMERCIAL_WORKER_ALLOW_PREVIEW=false
API_COMMERCIAL_POLL_SECONDS=10
API_COMMERCIAL_WORKER_STALE_SECONDS=90
API_COMMERCIAL_PRODUCTION_BRANCH=feature/turbo-short-term-btc
API_CREDIT_INVOICE_TTL_MINUTES=60
API_CREDIT_CONFIRMATION_SECONDS=20
```

Preview and non-production branches remain idle unless explicitly allowed.

`GET /api/v1/health` includes a `commercial` block with:

- launch and live-key gates;
- Treasury incoming gate;
- TON network;
- fresh commercial workers;
- pending and expired invoices;
- paid invoices and credits sold during the last 24 hours;
- pending live-access requests;
- stable warning codes.

## Live access

A project owner submits:

- product/use-case description;
- estimated monthly requests;
- acceptance of the current beta terms version.

Admin Center shows pending requests. Approval sets:

```text
commercial_status = live_enabled
live_keys_enabled = true
```

The global `API_LIVE_KEYS_ENABLED` gate must also be enabled before a user can issue a live key.

Live keys:

- use prefix `da_live_...`;
- are shown once;
- store only SHA-256 key hashes;
- support the same explicit scopes as test keys;
- count toward the existing per-project active-key limit;
- rotate without changing environment.

Rejecting a request keeps the project in test-only mode.

## Spend controls

Each API client has:

```text
monthly_spend_limit_credits
low_balance_threshold
```

A value of zero disables that control.

A PostgreSQL trigger locks the client and rejects a new credit reservation when current-month `reserved + charged` units plus the new reservation exceed the project limit. Refunded reservations do not consume the monthly limit.

The application translates the database guard into stable error:

```text
monthly_spend_limit_exceeded
```

The account response and Developer Portal expose current usage, remaining limit, balance, and low-balance state.

## Portal endpoints

These endpoints require an authenticated DeepAlpha web session. Mutations also require `X-DeepAlpha-Portal: 1`.

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

They are not bearer-key public API methods and are intentionally excluded from the public OpenAPI contract.

## Admin controls

Admin Center → API adds:

- commercial runtime health and worker heartbeat;
- package creation and price editing;
- manual TON payment scan;
- live-access approval and rejection;
- invoice history, references, statuses, transaction hashes, and errors.

## Production smoke

After deployment, use a dedicated API project:

1. verify `commercial-worker` heartbeat is fresh;
2. create a small explicit package in Admin Center;
3. create an invoice from the Developer Portal;
4. send the exact TON amount to the displayed Treasury address with the exact `api_pay_...` comment;
5. wait for confirmations and refresh the invoice;
6. verify invoice status `paid`;
7. verify one `purchase` ledger entry and one balance increase;
8. repeat payment scans and verify no second credit grant;
9. test wrong amount and wrong comment without automatic crediting;
10. set a monthly spend limit and verify the next over-limit job returns `monthly_spend_limit_exceeded`;
11. submit, approve, and issue a `da_live_...` key;
12. rotate it and verify the replacement remains live.

GitHub CI validates code and contract behavior but cannot prove Railway process startup, Treasury configuration, TON Center availability, or a real on-chain transfer.
