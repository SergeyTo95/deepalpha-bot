# Velia Quantum provider contract

Quantum is a product mode, independent of its inference host. Public clients use
`velia-quantum` (OpenAI gateway) or `chat_mode=quantum` (account API). The upstream
model identifier and credential never come from a client request.

`velia_quantum_provider.py` owns provider selection, request shaping, bounded
response decoding and public errors. `services/velia_quantum_service.py` connects
that contract to existing account history, streaming and PostgreSQL locks.
The original Qwen research/build branch and worker are not modified.

## Initial external route

Configure the account backend that serves the browser's identity authority:

```dotenv
VELIA_QUANTUM_ENABLED=true
VELIA_QUANTUM_PROVIDER=experiential
VELIA_QUANTUM_BASE_URL=https://api.experientiallabs.ai/v1
VELIA_QUANTUM_MODEL=glm-5.3-uncensored:free
EXPLABS_API_KEY=<secret configured by the operator>
```

`VELIA_QUANTUM_API_KEY` can replace `EXPLABS_API_KEY`. Keys for Flash, Kimi or any
other provider are never reused. Configure the same route separately on the
desktop gateway only if its direct `/desktop-api/v1/chat/completions` endpoint
also needs Quantum. Saved browser conversations call the account backend.

The browser advertises Quantum only when the authenticated backend returns
`features.chat_quantum=true`. It shows the provider attribution supplied by the
backend, and retains the Quantum label when reloading stored messages.
The server rechecks availability when sending a request. Guests and Browser
Agent retain their existing Flash route. Medical `medical_no_search` requests
retain their Flash-only restriction. This release accepts text; it rejects new
Quantum attachments before upload/generation rather than silently using Pro.

The Experiential adapter accepts only this exact host and `:free` model. A
missing key, bad configuration, quota exhaustion, authentication failure,
redirect, truncated stream or empty answer never switches to a paid model or
another provider. There are no automatic completion retries. The provider's
account/card verification and quota eligibility must be completed separately.

## Switch to a VELIA-hosted model

Change only the account backend's configuration (and the gateway's direct route,
if used):

```dotenv
VELIA_QUANTUM_ENABLED=true
VELIA_QUANTUM_PROVIDER=self_hosted
VELIA_QUANTUM_BASE_URL=http://quantum.railway.internal:8080/v1
VELIA_QUANTUM_MODEL=velia-owned-checkpoint
VELIA_QUANTUM_API_KEY=<worker secret>
```

The server must implement authenticated OpenAI Chat Completions. Streams use
`choices[].delta.content`, a terminal `finish_reason`, and `data: [DONE]`.
Tool round trips use standard `tool_calls` and `tool_call_id` fields. The adapter
omits GLM-specific `reasoning_effort` for self-hosted models. HTTP is accepted
only for loopback/Railway private hosts; public hosts require HTTPS. Redirects
are disabled. Custom runtimes exposing a different protocol need a new adapter
behind this boundary; they do not require changes to chat modes, history or UI.

Store a stable `provider=model=velia-quantum` identity in account messages across
provider changes, including pending/error rows. Existing idempotency keys must
not trigger another call or replay an answer under a different product mode.
Quantum attempts are excluded from the existing Pro message/cost budget.

## Bounds and release acceptance

| Setting | Default | Allowed range |
|---|---:|---:|
| `VELIA_QUANTUM_MAX_OUTPUT_TOKENS` | 8192 | 256–8192 |
| `VELIA_QUANTUM_MAX_CONTEXT_CHARS` | 96000 | 12000–256000 |
| `VELIA_QUANTUM_USER_DAILY_LIMIT` | 10 | 1–1000 |
| `VELIA_QUANTUM_GLOBAL_DAILY_LIMIT` | 50 | 1–10000 |

These are application bounds, not a promise to expose the provider's full 1M
context. The account path serializes reservations using a distinct Quantum
PostgreSQL advisory lock, applies existing user/conversation ownership checks,
counts failed attempts and reuses the account stream's bounded event queue.
The direct desktop endpoint retains its separate preview budget and allowlist.
Provider response data is bounded; separate reasoning and provider metadata are
removed before browser delivery. This is an external inference route, so the
provider processes the conversation text submitted to Quantum.

Saved browser streams send bounded `progress` metadata during preparation,
followed by ordinary answer deltas. Preparation heartbeats keep the connection
live, and disconnecting cancels pending preparation and releases the gateway
reservation. These events never become answer text or stored chat history.
Unambiguous greetings, identity/capability questions and literal arithmetic can
skip the separate Flash interpreter even in an existing conversation; the full
history still reaches the selected answering model. Mixed, contextual, medical
and current-information requests keep the existing interpretation/retrieval
path. Timing logs contain only the model, phase durations and outcome, without
submitted text, account identifiers or credentials.

Before enabling, run provider/gateway/browser tests, PostgreSQL persistence and
quota tests with a dedicated `VELIA_FLASH_TEST_DATABASE_URL`, then one live
`:free` request using the operator's key. Confirm catalog visibility, reload,
mode isolation and quota errors. Local fixture success does not establish live
GLM quality, latency or free quota availability. Roll back by setting
`VELIA_QUANTUM_ENABLED=false`; stored Quantum history remains readable.
