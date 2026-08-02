# VELIA Mobile Chat beta

This contour exposes a provider-neutral native Android chat API. The Android app never receives Kimi, Gemini, database, Railway, or other infrastructure credentials.

## Safe rollout flags

All new endpoints fail closed by default.

```env
VELIA_MOBILE_API_ENABLED=false
VELIA_CHAT_ENABLED=false

# Comma-separated Telegram/DeepAlpha user IDs allowed during the closed beta.
VELIA_CHAT_BETA_USER_IDS=

# Dedicated text provider for VELIA Chat.
LLM_PROVIDER_VELIA_CHAT=kimi

# Short access tokens and rotating refresh tokens.
VELIA_MOBILE_ACCESS_TTL_SECONDS=900

# Finite-session rollout. Keep this explicit when a 30-day absolute limit is required.
VELIA_MOBILE_PERSISTENT_SESSIONS=false
VELIA_MOBILE_REFRESH_TTL_DAYS=30
VELIA_MOBILE_PAIRING_TTL_SECONDS=600

# Bounded context and response size.
VELIA_CHAT_MAX_INPUT_CHARS=12000
VELIA_CHAT_CONTEXT_MESSAGES=24
VELIA_CHAT_CONTEXT_CHARS=24000
VELIA_CHAT_MAX_OUTPUT_TOKENS=1536

# Closed-beta financial circuit breakers.
VELIA_CHAT_MAX_MESSAGES_PER_USER_DAY=100
VELIA_CHAT_PER_USER_DAILY_COST_USD_LIMIT=2.00
VELIA_CHAT_DAILY_COST_USD_LIMIT=10.00
VELIA_CHAT_REQUEST_COST_RESERVE_USD=0.25

# Pending assistant generations older than this lease are recovered as errors.
# Keep this above the maximum provider timeout/retry window.
VELIA_CHAT_PENDING_LEASE_SECONDS=600

# Temporary diagnostics for named beta users only.
VELIA_MOBILE_DEBUG_USAGE=true
VELIA_MOBILE_DEBUG_USER_IDS=
```

For the Android product behavior where an authenticated device remains connected until explicit logout or security revocation, set:

```env
VELIA_MOBILE_PERSISTENT_SESSIONS=true
```

In persistent mode `VELIA_MOBILE_REFRESH_TTL_DAYS` is intentionally ignored. Access tokens remain short-lived, refresh tokens rotate, and logout, replay detection, device mismatch, or administrative revocation still terminate the server session.

Existing Kimi transport configuration remains server-side:

```env
KIMI_ENABLED=true
KIMI_API_KEY=<Railway secret>
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k3
KIMI_REASONING_EFFORT=high
KIMI_TIMEOUT_SECONDS=120
KIMI_MAX_RETRIES=1
LLM_FALLBACK_PROVIDER=gemini
```

## Pairing the first Android device

1. Sign in to the production WebApp through its existing Telegram or Google flow.
2. Open `https://deepalpha-ai.com/mobile-connect` in the same browser.
3. Copy the one-time pairing code into the native Android login screen.
4. The Android app exchanges the code once for a short-lived opaque access token and rotating refresh token.
5. Both tokens are stored with Android encrypted storage. Refresh-token replay revokes the entire mobile session.

The pairing page never exposes the browser cookie or Kimi API key.

## API surface

```text
GET    /mobile-api/v1/health
POST   /mobile-api/v1/auth/exchange
POST   /mobile-api/v1/auth/refresh
POST   /mobile-api/v1/auth/logout
GET    /mobile-api/v1/me
GET    /mobile-api/v1/conversations
POST   /mobile-api/v1/conversations
GET    /mobile-api/v1/conversations/{id}
PATCH  /mobile-api/v1/conversations/{id}
DELETE /mobile-api/v1/conversations/{id}
GET    /mobile-api/v1/conversations/{id}/messages
POST   /mobile-api/v1/conversations/{id}/messages
GET    /mobile-api/v1/usage
```

Every message write requires an `Idempotency-Key` so Android retries cannot create duplicate provider charges. Only one physical generation per user is allowed across all WebApp workers and replicas. The per-user PostgreSQL advisory lock is held through the provider call; competing requests fail with `generation_in_progress` instead of creating a second charge.

The blocking provider request runs outside the aiohttp event loop. Health, authentication, Developer API and WebApp requests therefore remain responsive during a slow Kimi retry window.

If a process terminates after persisting a pending assistant row, a later request marks the row `generation_abandoned` after `VELIA_CHAT_PENDING_LEASE_SECONDS`. History endpoints return the newest bounded message window in chronological order.

## Cost measurement

The existing Kimi gateway remains the source of truth for physical provider calls. Each completed assistant message stores:

- prompt tokens;
- completion tokens;
- cached input tokens;
- hidden reasoning-token count when reported by the provider;
- estimated USD cost;
- latency;
- finish reason;
- internal provider/model identifiers.

Provider and model identifiers are not returned through the public mobile response. Token/cost diagnostics are returned only when the debug flag is enabled for the authenticated beta user.

## Deliberately excluded from the first beta

- image generation;
- speech recognition and synthesis;
- plugin execution;
- streaming/SSE;
- Google Play Billing;
- public registration;
- production subscription enforcement.

The first beta validates text quality, context behavior, retries, session rotation, history sync, latency, and real cost before prices or public limits are finalized.
