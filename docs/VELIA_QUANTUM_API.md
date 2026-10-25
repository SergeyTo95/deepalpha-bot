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
desktop gateway for Quantum's own retrieval planning and its direct
`/desktop-api/v1/chat/completions` endpoint. A service-local Railway reference
can supply the gateway credential without reading it back. Saved browser
answers still call the production account backend and retain its history.

The browser advertises Quantum only when the authenticated backend returns
`features.chat_quantum=true`. It shows the supplied provider attribution and
retains the Quantum label when reloading stored messages. The server rechecks
availability on every request. Voice conversation, ordinary chat, medical
document actions and Internet retrieval keep the explicitly selected model.
Flash turns never invoke the optional Decision model; Quantum turns never
invoke Flash or Decision. An unavailable selection fails without switching.
Guests remain Flash-only. Browser Agent currently requires Flash; enabling it
while Quantum is selected is refused without changing the user's model.

## Selected-model voice and files

Voice recognition and speech synthesis use the existing browser/configured
speech engines. The selected language model prepares and answers the turn.
The voice dialog displays its model. Voice turns can use the same model's
Internet plan, keep source links, and request short spoken answers. Flash
retains its 128-token voice fast path; Quantum uses a 1024-token bound because
its reasoning and final answer share the upstream output budget.

Self-contained voice greetings, casual check-ins, literal arithmetic and clearly
creative story requests skip browser history retrieval and model-based planning;
the native selected-model answer still uses its existing conversation history.
Current facts, sourced stories, mixed requests and medical questions retain
their normal selected-model retrieval path. Final speech recognition submits
immediately without waiting for a delayed engine shutdown event. Playback can
start at a complete clause or bounded word boundary in a long streamed sentence.

Browser voice output is a separate speech adapter, independent of the selected
language model. `VELIA_WEB_TTS_PROVIDER=edge` enables three actual female speaker
IDs: Svetlana (Russian), Emma and Ava (multilingual, including Russian). They are
distinct upstream voices, not pitch aliases of the seven Android device presets.
The existing SpeechKit adapter stays opt-in and needs its own key. No paid speech
adapter is activated automatically. Edge uses an online consumer speech service
through the pinned `edge-tts` adapter, not a contractual Azure Speech API; remote
availability and startup latency can vary. The fast device voice remains the
automatic default when available, and the user can explicitly select each neural
voice. Text is sent to that speech provider only when a remote voice is selected.
Voice errors retain the saved answer and do not silently switch the speaker.
No voice model packs are repeatedly downloaded. The three remote speakers were
confirmed against the live provider inventory and synthesized with Russian
synthetic text. See the [adapter](https://github.com/rany2/edge-tts) and
[voice language metadata](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support).

`python -m desktop.probe_web_voice` is a one-time deployment acceptance for the
configured online speech provider: fixed synthetic text, verified female speaker
inventory, bounded MP3 audio and timings without submitted user text or keys.
Remove that extra command after successful release; preserve the existing Browser
Agent pre-deploy gate. Only the three bounded timing loggers are enabled in the
gateway entry point; they log model/voice IDs, timings and outcomes, not content.

Voice playback prefetches only the next phrase while the current phrase is
downloaded/played (at most two requests in flight). Stop, speaker changes and
cancellation abort unused downloads; late results cannot start playback.
Punctuation-only streamed tails never reach TTS. The unbilled Edge speech adapter
may retry an empty-audio result once with the same voice, only before any audio
bytes arrived, within the existing overall deadline. Paid speech adapters and
language-model completions do not gain automatic retries. Model failures surface
their existing localized reason in the voice window; speech failures keep the
saved text and remain distinct from model failures.

The account stream reuses the Quantum capability freshly revalidated by that
request's session middleware, removing a duplicate identity HTTP round trip.
Native availability, ownership and quota checks remain independent. Quantum
planning sends the complete annotated function schema on its single request.
Complete tool envelopes with `stop` or `tool_calls` are accepted only with one
correct named call and validated arguments. Object arguments and absent/null
optional metadata are normalized without repairing searches, proposed term
changes or medical evidence. Truncated, malformed, extra-field and invented-span
responses still fail closed. Failure telemetry includes a bounded stage/code,
never submitted text or raw provider bodies.

For a one-time voice release gate use
`python -m desktop.probe_quantum_independence --voice`: three fixed synthetic
planning cases (direct, current-information search, official medical scope),
Flash/Decision forbidden, and a fixed Ava synthesis request. It writes no account
history and makes no search-provider request. Only the exact free Quantum route
is accepted. Remove the extra probe after successful release, retaining the
existing Browser Agent gate.

Enable `VELIA_FILE_ANALYST_ENABLED=true` and
`VELIA_FLASH_ATTACHMENTS_ENABLED=true` on the account backend. The deployment
includes Tesseract, Poppler and English/Russian/Turkish OCR data. TXT, MD, CSV,
JSON, DOCX and text PDFs are decoded locally. Photos and scanned PDF pages use
local OCR, not another language model. **This recognizes text; it does not
provide general photo/scene understanding.** The UI labels image uploads as
text recognition and explains unreadable images rather than invoking a vision
provider. The selected Flash/Quantum model receives only extracted untrusted
text and produces the answer. Original uploads retain the existing transient
processing, quota, ownership, deletion and metadata contracts.

Browser uploads must declare `X-Velia-Chat-Mode: flash|quantum|pro`. Flash and
Quantum uploads require the authenticated backend's
`features.chat_selected_model_inputs=true` capability, so an old backend cannot
silently route them to its legacy file vision provider. New clients also check
`quantum_attachments` before enabling Quantum files. Missing selection or an
old backend fails closed. Strict uploads are forwarded to the separate native
`/mobile-api/v1/conversations/{id}/attachments/selected` endpoint. Old replicas
return 404 for this route, so a rolling deployment cannot accidentally invoke
legacy vision after a capability read reached a newer replica. Legacy native
clients without this header keep their
existing attachment contract; the browser gateway requires an explicit mode.
Upload idempotency is namespaced by mode. Turn replay compares explicit file
sets, including empty sets. Long document context preserves the user's question
and uses bounded, complete untrusted frames with an explicit truncation marker.

`VELIA_FILE_OCR_LANGUAGES` defaults to `eng+rus+tur`.
`VELIA_ATTACHMENTS_OCR_MAX_PAGES` defaults to 8 (range 1–20); text PDF page bounds
remain separate. OCR subprocesses are limited to two concurrent jobs, with
bounded execution and temporary files removed on completion/failure. Busy,
missing, timed-out or unreadable OCR never triggers another model. Upload
progress is visible, and cancelling keeps the same idempotency key for recovery.

Deploy the account backend first, then the browser gateway. Keep existing
release gates. `python ci/probe-selected-model-inputs.py` checks real local
image/PDF OCR without external calls or user data. A one-time
`python ci/probe-velia-quantum.py --voice` accepts the free Quantum voice path;
remove that one-time API probe after acceptance to avoid recurring quota use.

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
skip separate interpretation even in an existing conversation; the full
history still reaches the selected answering model. All other Quantum turns
use Quantum itself to select a validated retrieval/clarification plan. They
never call the Flash interpreter, Flash worker or Decision router. Existing
quoted-evidence validation, official-health domains and retrieval-failure
handling are retained. A planned Internet turn can use one bounded Quantum
planning call plus the answer call; there are no completion retries or model
fallbacks. The gateway therefore uses the same configured Quantum provider
contract as the answer backend. Automatic retrieval requires standard JSON
tool calls; a text-only self-hosted runtime can be used with retrieval disabled.
Timing logs contain only the model, phase durations and outcome, without
submitted text, account identifiers or credentials.

Before enabling, run provider/gateway/browser tests, PostgreSQL persistence and
quota tests with a dedicated `VELIA_FLASH_TEST_DATABASE_URL`, then one live
`:free` request using the operator's key. Confirm catalog visibility, reload,
mode isolation and quota errors. Local fixture success does not establish live
GLM quality, latency or free quota availability. Roll back by setting
`VELIA_QUANTUM_ENABLED=false`; stored Quantum history remains readable.
