# Velyon Memory — Railway service

This directory deploys the private long-term memory runtime used by the VELIA shadow pilot.

It is an internal infrastructure service. It must not receive a public domain and must not be called directly by Android clients.

## Railway service

Create a new service in the same Railway project and environment as `deepalpha-bot`:

- service name: `velyon-memory`;
- source repository: `SergeyTo95/deepalpha-bot`;
- branch: `feature/turbo-short-term-btc`;
- root directory: `/deploy/velyon-memory`;
- replicas: `1` during the pilot;
- public domain: disabled;
- persistent volume mount path: `/data/tdai-memory`.

The Dockerfile exposes port `8420`, stores all durable runtime data below `/data/tdai-memory`, and inherits the upstream runtime entrypoint.

## Required service variables

```dotenv
RAILWAY_RUN_UID=0
TDAI_GATEWAY_API_KEY=<strong-random-secret-at-least-32-characters>
TDAI_LLM_BASE_URL=<internal-openai-compatible-base-url>
TDAI_LLM_API_KEY=<private-memory-llm-key>
TDAI_LLM_MODEL=<private-memory-model-id>
```

Railway mounts attached volumes as `root`. `RAILWAY_RUN_UID=0` is therefore required so the runtime can create and update its SQLite database on the mounted volume. Outside Railway, the image keeps its non-root default user.

Do not reuse the Android token, Telegram token, image key, database password, or public API credentials.

The memory LLM credentials are used only by the asynchronous extraction pipeline. They are never returned by the VELIA mobile API.

## Runtime defaults

The container configures:

```dotenv
TDAI_GATEWAY_CONFIG=/data/config/tdai-gateway.yaml
TDAI_GATEWAY_HOST=::
TDAI_GATEWAY_PORT=8420
TDAI_DATA_DIR=/data/tdai-memory
TDAI_DEPLOY_MODE=standalone
STATE_BACKEND=local
```

Binding to `::` supports Railway environments whose private DNS resolves to IPv6 as well as current dual-stack environments.

The standalone pilot uses:

- local SQLite and local files on the Railway volume;
- BM25 retrieval without a remote embedding service;
- one service replica;
- asynchronous L0 → L1 → L2 → L3 processing;
- no Skill extraction.

## Backend variables

Add these variables to the existing `deepalpha-bot` service, but keep capture disabled during the first deployment:

```dotenv
VELIA_MEMORY_SHADOW_ENABLED=false
VELIA_MEMORY_SHADOW_WORKER_ENABLED=true
VELIA_MEMORY_SHADOW_USER_IDS=5811340792
VELIA_MEMORY_SHADOW_ALLOW_ALL=false

VELIA_MEMORY_ENDPOINT=http://velyon-memory.railway.internal:8420
VELIA_MEMORY_API_KEY=<same-value-as-TDAI_GATEWAY_API_KEY>
VELIA_MEMORY_SERVICE_ID=velia-production
VELIA_MEMORY_TEAM_ID=velia
VELIA_MEMORY_AGENT_ID=velia-main

VELIA_MEMORY_CONNECT_TIMEOUT_SECONDS=2
VELIA_MEMORY_READ_TIMEOUT_SECONDS=8
VELIA_MEMORY_SHADOW_MAX_ATTEMPTS=8
VELIA_MEMORY_SHADOW_POLL_SECONDS=1
VELIA_MEMORY_MAX_MESSAGE_CHARS=50000
```

The internal Railway endpoint uses HTTP inside the private project network. Do not substitute a generated public URL.

## Safe activation order

1. Deploy `velyon-memory` with its volume and required variables.
2. Confirm its Railway deployment is healthy and the runtime can write under `/data/tdai-memory`.
3. Deploy `deepalpha-bot` with the memory variables while `VELIA_MEMORY_SHADOW_ENABLED=false`.
4. Confirm logs contain `VELIA_MEMORY_SHADOW_RUNTIME_PATCH_INSTALLED` and that the worker remains healthy.
5. Set `VELIA_MEMORY_SHADOW_ENABLED=true` only after both services are healthy.
6. Keep `VELIA_MEMORY_SHADOW_USER_IDS=5811340792` and `VELIA_MEMORY_SHADOW_ALLOW_ALL=false` during the pilot.
7. Send several normal VELIA messages and inspect delivery metadata in the backend logs and outbox table.

## Expected logs

Memory service should report a healthy gateway listening on port `8420`.

Backend:

```text
VELIA_MEMORY_SHADOW_RUNTIME_PATCH_INSTALLED
VELIA_MEMORY_SHADOW_WORKER_STARTED
VELIA_MEMORY_SHADOW_ENQUEUED
VELIA_MEMORY_SHADOW_DELIVERY ... success=True
```

No message text, API keys, or memory LLM credentials should appear in logs.

## Health and monitoring

The memory container health check calls:

```text
http://[::1]:8420/health
```

Outbox status:

```sql
SELECT status, COUNT(*)
FROM velia_memory_shadow_outbox
GROUP BY status
ORDER BY status;
```

Recent terminal failures:

```sql
SELECT event_id, user_id, conversation_id, attempt_count,
       response_status, last_error, updated_at
FROM velia_memory_shadow_outbox
WHERE status='failed'
ORDER BY updated_at DESC
LIMIT 50;
```

## Pilot rollback

Set:

```dotenv
VELIA_MEMORY_SHADOW_ENABLED=false
```

and redeploy `deepalpha-bot`.

VELIA chat immediately continues without new memory capture. Existing outbox rows and memory data remain available for inspection. The feature does not participate in prompt construction during the shadow phase.

## Production hardening after the pilot

Before wider rollout:

- pin the base image to a reviewed immutable version or digest;
- implement user-visible memory review, deletion, and opt-out controls;
- add a retention policy for raw L0 conversations;
- test volume backup and restore;
- keep one replica unless the storage backend is migrated away from local SQLite;
- enable memory recall only through a separate feature flag and allowlist.
