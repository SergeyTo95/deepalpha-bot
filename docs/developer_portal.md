# DeepAlpha User Developer Portal

DeepAlpha users can manage Developer API access from Telegram:

```text
Profile -> Developer API -> Open API portal
```

The portal is served at:

```text
/developer
```

It uses the existing DeepAlpha WebApp session. When opened as a Telegram WebApp, it submits Telegram `initData` to the existing `/api/auth/telegram` endpoint and receives the normal HttpOnly `deepalpha_session` cookie.

## User capabilities

A signed-in user can:

- create an API project;
- view project credits and request limits;
- issue test API keys;
- select self-service scopes;
- view key prefixes, status, and last use;
- rotate an active key;
- revoke an active key;
- inspect recent credit ledger entries;
- view current API product pricing;
- read quick-start documentation and endpoint examples.

## Ownership

The `api_client_owners` table maps a DeepAlpha user to an API client:

```text
user_id -> client_id -> role
```

Every self-service read and mutation joins through this ownership table. Request bodies cannot select another `user_id`.

Each API client currently has one owner. The schema can later be expanded for team members after roles and invitations are implemented.

Default self-service limits:

```text
3 API projects per user
5 active keys per project
100 requests per day
2,000 requests per month
30 requests per minute
```

These defaults are configurable:

```env
DEVELOPER_PORTAL_MAX_PROJECTS=3
DEVELOPER_PORTAL_MAX_KEYS_PER_PROJECT=5
DEVELOPER_PORTAL_DEFAULT_DAILY_LIMIT=100
DEVELOPER_PORTAL_DEFAULT_MONTHLY_LIMIT=2000
DEVELOPER_PORTAL_DEFAULT_RATE_LIMIT=30
```

## Test keys only

The user portal currently issues only:

```text
da_test_...
```

Live keys remain an administrator-controlled capability until public paid analysis execution is enabled.

Self-service scopes:

- `account:read`
- `usage:read`
- `analysis:run`
- `analysis:read`
- `opportunities:read`
- `markets:read`

The public analysis routes are still disabled, so `analysis:*` and `opportunities:read` prepare future access but do not yet execute paid analysis.

The portal does not expose:

- wallet sends;
- withdrawals;
- admin scopes;
- webhook management;
- live key issuance.

## Secret handling

A raw API key is returned only by the issue or rotate mutation. The portal displays it in a modal once and clears it from the DOM when the modal closes.

The raw key is never:

- stored in PostgreSQL;
- included in project overview responses;
- sent to Telegram messages;
- stored in browser local storage or session storage.

Only the SHA-256 hash and public prefix remain on the server.

## App API routes

All routes require the existing HttpOnly WebApp session:

```http
GET  /app-api/v1/developer/overview
POST /app-api/v1/developer/projects
POST /app-api/v1/developer/projects/{client_id}/keys
POST /app-api/v1/developer/keys/{key_id}/rotate
POST /app-api/v1/developer/keys/{key_id}/revoke
```

Mutations additionally require:

```http
Content-Type: application/json
X-DeepAlpha-Portal: 1
```

The custom header forces cross-origin browser requests through CORS preflight. The security middleware covers both `/api/` and `/app-api/` paths and rejects unknown origins.

## Current Developer API methods

Keys can currently call:

```http
GET /api/v1/account
GET /api/v1/usage
GET /api/v1/capabilities
```

Public execution remains disabled:

```http
POST /api/v1/analyses
GET  /api/v1/analyses/{job_id}
GET  /api/v1/opportunities
```

The next API phase will connect those routes to the prepared billing reservation and settlement lifecycle.
