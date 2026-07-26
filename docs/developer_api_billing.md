# DeepAlpha Developer API Billing

The Developer API uses independent API credits. Telegram user tokens and API credits are intentionally separate products with separate pricing and accounting.

Public analysis execution remains disabled in this phase. The billing layer is prepared before execution is exposed.

## Products

Default products are inserted only when missing and remain editable from DeepAlpha Admin Center:

| Product code | Default price |
|---|---:|
| `opportunity_scan` | 1 credit |
| `market_data` | 1 credit |
| `quick_analysis` | 10 credits |
| `deep_analysis` | 50 credits |

An administrator may change a price or disable a product without deploying new code.

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

## Atomic job creation

`create_billed_api_job(...)` performs one database transaction:

1. locks the API client row with `FOR UPDATE`;
2. checks the client and product status;
3. finds any existing reservation for the same idempotency key;
4. compares a canonical SHA-256 request fingerprint;
5. verifies the available balance;
6. deducts the product price;
7. creates the credit reservation;
8. creates the queued API job;
9. appends the reserve ledger entry;
10. commits all changes together.

No job is created without a matching reservation, and no reservation is committed without its ledger entry.

## Idempotency

Future paid execution requests must include:

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

The job result and charge finalization are committed together. Repeating success settlement is idempotent.

On internal failure:

```text
reserved/charged → refunded
```

The client's available balance is increased by the reservation amount, the job is marked as error, and one refund entry is appended. Repeating failure settlement is idempotent.

## Admin controls

The API section now supports:

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

It reuses:

- `api_clients`;
- `api_keys`;
- `api_jobs`;
- `api_audit_log`.

## Next phase

The next phase may safely register:

```http
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
```

That phase still needs the execution worker, stable public result schema, active-job concurrency limits, timeouts, and signed webhook delivery.