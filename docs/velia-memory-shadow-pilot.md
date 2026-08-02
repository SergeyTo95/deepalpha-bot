# VELIA memory shadow pilot

## Purpose

The first memory rollout is deliberately **shadow-only**.

Completed VELIA chat turns are copied into a separate private Velyon memory service, but recalled memory is not inserted into prompts and cannot change user-visible answers. This lets us validate extraction quality, isolation, latency, reliability, and cost before memory affects production conversations.

## Architecture

```text
VELIA chat response succeeds
        |
        +-- user receives the normal response
        |
        +-- completed turn is inserted into a PostgreSQL outbox
                 |
                 +-- dedicated shadow worker
                          |
                          +-- private Velyon memory service
```

The outbox is durable and idempotent by assistant message ID. A temporary memory outage does not delay or fail a successful VELIA answer. Retryable failures use bounded exponential backoff, stale worker leases are recovered, and non-retryable configuration or authorization errors become terminal for that event.

## Isolation

Every memory write contains four isolation dimensions:

- `team_id`: VELIA product boundary;
- `agent_id`: Velia assistant boundary;
- `user_id`: the authenticated VELIA user ID;
- `session_id`: the VELIA conversation ID.

The pilot is fail-closed. Enabling the global feature does not capture anyone until a user ID is explicitly allowlisted. An all-user rollout requires a second independent switch.

## Required private service

Run the memory runtime as a separate service with:

- one replica during the pilot;
- a persistent volume for its local database and files;
- private-network access only;
- a strong gateway API key;
- no public domain;
- its own internal LLM credentials for asynchronous memory extraction.

Do not run the memory runtime inside each `deepalpha-bot` process. Its local database and in-process pipeline require a single durable service instance.

## Backend environment variables

Start with the feature disabled:

```dotenv
VELIA_MEMORY_SHADOW_ENABLED=false
VELIA_MEMORY_SHADOW_WORKER_ENABLED=true
VELIA_MEMORY_SHADOW_USER_IDS=5811340792
VELIA_MEMORY_SHADOW_ALLOW_ALL=false

VELIA_MEMORY_ENDPOINT=http://velyon-memory.railway.internal:8420
VELIA_MEMORY_API_KEY=<strong private gateway key>
VELIA_MEMORY_SERVICE_ID=velia-production
VELIA_MEMORY_TEAM_ID=velia
VELIA_MEMORY_AGENT_ID=velia-main

VELIA_MEMORY_CONNECT_TIMEOUT_SECONDS=2
VELIA_MEMORY_READ_TIMEOUT_SECONDS=8
VELIA_MEMORY_SHADOW_MAX_ATTEMPTS=8
VELIA_MEMORY_SHADOW_POLL_SECONDS=1
VELIA_MEMORY_MAX_MESSAGE_CHARS=50000
VELIA_MEMORY_TLS_VERIFY=true
```

For an initial Railway private HTTP endpoint, TLS verification is not used because the request stays on the private network. Keep `VELIA_MEMORY_TLS_VERIFY=true` for HTTPS endpoints.

After the memory service health check succeeds and the variables are present, enable only the allowlisted pilot account:

```dotenv
VELIA_MEMORY_SHADOW_ENABLED=true
```

Never set `VELIA_MEMORY_SHADOW_ALLOW_ALL=true` during the first pilot.

## Outbox states

Table: `velia_memory_shadow_outbox`

- `pending`: captured and waiting;
- `delivering`: leased by the worker;
- `retrying`: temporary failure with a future retry time;
- `succeeded`: accepted by the memory service;
- `failed`: terminal or exhausted retries.

Operational snapshot:

```sql
SELECT status, COUNT(*)
FROM velia_memory_shadow_outbox
GROUP BY status
ORDER BY status;
```

Recent failures:

```sql
SELECT event_id, user_id, conversation_id, attempt_count,
       response_status, last_error, updated_at
FROM velia_memory_shadow_outbox
WHERE status='failed'
ORDER BY updated_at DESC
LIMIT 50;
```

Do not expose raw captured content through public logs or APIs.

## Expected logs

```text
VELIA_MEMORY_SHADOW_RUNTIME_PATCH_INSTALLED
VELIA_MEMORY_SHADOW_WORKER_STARTED
VELIA_MEMORY_SHADOW_ENQUEUED
VELIA_MEMORY_SHADOW_DELIVERY ... success=True
VELIA_MEMORY_SHADOW_QUEUE ...
```

Logs contain internal IDs and status metadata, but not API keys or message content.

## Pilot acceptance criteria

Keep recall disabled until all of the following are true:

1. no cross-user or cross-conversation isolation defects;
2. normal VELIA answers remain available when memory is offline;
3. at least 95% of eligible turns reach `succeeded` without manual action;
4. extracted facts are reviewed and are materially useful;
5. sensitive or transient statements are not incorrectly promoted into stable memory;
6. memory extraction cost and latency are measured;
7. deletion and user opt-out behavior are designed before wider rollout.

## Next phase

The next PR should add recall behind a separate feature flag and pilot allowlist. Prompt construction should use:

1. VELIA system rules;
2. explicit user profile;
3. bounded recalled memory;
4. the latest unsummarized chat turns;
5. the current request.

Explicit profile data and the current user message must always override automatically extracted memory. Memory retrieval must remain fail-open and have a strict character, item, and timeout budget.
