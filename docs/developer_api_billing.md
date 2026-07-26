# DeepAlpha Developer API Billing

The Developer API uses independent API credits. Telegram user tokens and API credits are intentionally separate products with separate pricing and accounting.

Quick Analysis now uses this billing lifecycle in production API jobs.

## Products

Default products are inserted only when missing and remain editable from DeepAlpha Admin Center:

| Product code | Default price |
|---|---:|
| `opportunity_scan` | 1 credit |
| `market_data` | 1 credit |
| `quick_analysis` | 10 credits |
| `deep_analysis` | 50 credits |

An administrator may change a price or disable a product without deploying new code.

Only `quick_analysis` is publicly executable in the current API phase.

## Balance model

`api_clients.credit_balance` is the client's currently available balance.

The append-only `api_credit_ledger` is the authoritative audit trail. Every entry contains:

- client ID;
- event type;
- signed balance delta;
- balance after the event;
- immutable idempotency key;
- optional reservation and job IDs;
- metadata and creation time.

The database rejects updates and deletes against the ledger.

## Ledger events

Current event types:

- `opening_balance` — migration snapshot for a pre-existing non-zero balance;
- `admin_grant` — administrator adds credits;
- `admin_debit` — administrator removes credits;
- `reserve` — job creation removes credits from the available balance;
- `charge` — successful job finalizes an existing reservation with a zero balance delta;
- `refund` — internal failure returns reserved credits.

A charge has a zero delta because the available balance was already reduced atomically during reservation.

## Atomic Quick Analysis job creation

`POST /api/v1/analyses` validates the request and calls `create_billed_api_job(...)`, which performs one database transaction:

1. locks the API client row with `FOR UPDATE`;
2. checks the client and `quick_analysis` product status;
3. finds any existing reservation for the same idempotency key;
4. compares a canonical SHA-256 request fingerprint;
5. verifies the available balance;
6. deducts the current product price;
7. creates the credit reservation;
8. creates the queued API job;
9. appends the reserve ledger entry;
10. commits all changes together.

No job is created without a matching reservation, and no reservation is committed without its ledger entry.

Submissions are also serialized per API client before checking the active-job limit. The current default is two queued or running jobs.

## Idempotency

Every paid Quick Analysis request must include:

```http
Idempotency-Key: request_01J...
```

The combination `(client_id, idempotency_key)` is unique.

Repeating the same key with the same canonical request payload returns the existing job and does not reserve credits again.

Repeating the same key with a different product or payload returns `idempotency_conflict`.

Keys must be 8–200 characters and may contain letters, numbers, `_`, `-`, `.`, `:`, with an alphanumeric first character.

## Success and failure settlement

On success:

```text
reserved → charged
```

The normalized public result and charge finalization are committed together. Repeating success settlement is idempotent.

On internal failure or terminal worker recovery:

```text
reserved/charged → refunded
```

The client's available balance is increased by the reservation amount, the job is marked as error, and one refund entry is appended. Repeating failure settlement is idempotent.

The worker uses database leases. A stale running job is retried when attempts remain; otherwise its reservation is refunded.

## Request usage versus credit ledger

`api_usage.units` records billable units associated with the accepted API request. It does not replace the credit ledger.

- a new accepted Quick Analysis request records the reserved units;
- an idempotent replay records zero additional units;
- the ledger remains the source of truth for balance movement and settlement.

## Admin controls

The API section supports:

- editing product prices and enabled status;
- creating clients with a ledger-backed opening grant;
- adding or removing client credits;
- mandatory idempotency for manual adjustments;
- viewing recent ledger entries;
- viewing recent job reservations;
- existing key issuance, scopes, limits, usage, and revocation.

A negative manual adjustment is rejected when it would make the balance negative.

## Tables

Billing adds:

- `api_products`;
- `api_credit_reservations`;
- `api_credit_ledger`.

Quick Analysis execution extends `api_jobs` with:

- progress;
- worker ID;
- attempt count;
- start and finish timestamps;
- heartbeat and lease timestamps.

It also reuses:

- `api_clients`;
- `api_keys`;
- `api_audit_log`.

## Next billing integrations

The prepared billing lifecycle can next be reused by:

```http
GET /api/v1/opportunities
POST /api/v1/analyses with mode=deep
```

Signed webhook delivery will not create a second charge; it will report the result of the already settled job.
