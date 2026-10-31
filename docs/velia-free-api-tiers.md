# Free API tiers — prepared rollout

Set `VELIA_FREE_API_TIERS_ENABLED=true` only with the existing Z.ai Standard API key present. The flag is opt-in; disabled keeps the previous deployment behavior. Enable it on the account backend first and on the gateway after the backend is healthy.

| Public mode | Pinned upstream | Text | Voice |
| --- | --- | --- | --- |
| VELIA Flash | Z.ai `glm-4.5-flash` | Thinking disabled | Same model, short spoken answer |
| VELIA Quantum | Z.ai `glm-4.7-flash` | Thinking disabled | Same model, short spoken answer |
| VELIA PRO | Z.ai `glm-4.7-flash` | Thinking for tasks, 8192-token budget | Same model, thinking for tasks, 4096-token budget |

Z.ai lists both selected GLM models at zero token price. Quantum and PRO share GLM-4.7-Flash; PRO enables thinking for text and voice tasks, which may help complex tasks but increases latency and does not guarantee better answers. Only a bounded allowlist of self-contained social greetings disables thinking on the same PRO model and caps completion at 1024 tokens. Confirmations, continuations, mixed greetings plus tasks, attachments and unknown languages retain normal reasoning. This adds no classifier/model call and does not shorten conversation history. Shared native-turn instructions require feminine self-reference in all modes without rewriting quotations or the user’s gender. Provider queuing and overload can still delay any request. PRO uses a larger bounded completion budget because thinking consumes tokens; spoken answers remain short. No Groq key is required for these tiers. This is not a promise of unlimited quota or zero hosting cost. No paid model ID, provider built-in search or Coding Plan endpoint is selected. Text errors stop the selected route; voice reserves require the explicit configuration below.

Normal chat uses one streaming completion, or two when the selected model calls one bounded local web-search function. Existing search keys/quotas remain; no new paid search integration is enabled. No separate Flash planner, Decision call, Bonsai prompt render/tokenize request, or answer-review model call precedes normal free-tier chat. Desktop function tools still use validated tool messages on the pinned selected model. No new Railway service or project is needed.

The browser speech input and device/Edge output pipeline are unchanged. Voice mode passes the selected mode through to its own model, uses account-language defaults, and preserves the existing call lifecycle and transcript corrections. Native inference is not a speed guarantee: measure physical voice latency and multilingual answer quality after deployment.

## Voice provider timeout recovery

The account backend, not the browser gateway, executes saved-chat model requests.
Configure reserves on that exact service and environment. A Groq key configured
only on the preview gateway does not provide a reserve for production chat.

For the existing free Groq account, add `GROQ_API_KEY` to the existing production
`deepalpha-bot` service through Railway Variables, and set
`VELIA_VOICE_GROQ_FREE_ACCOUNT=true` and `VELIA_VOICE_RESERVES_ENABLED=true` there.
Keep the key server-side. OAuth tools return variable names only; do not replace
an inaccessible key with an empty value or an unresolved cross-environment
reference. Railway service/shared-variable references must resolve in the target
environment. The reserve remains inactive until an actual key is present.

Free-tier voice turns bound initial response-header waiting to 12 seconds after
connection (connect timeout at most 3 seconds). Before accepting any response,
a timeout can advance once to each explicitly configured free reserve; an
unresponsive route is skipped for 30 seconds. With no usable reserve the request
returns the selected mode's timeout error instead of waiting the old 60 seconds.
Text requests keep their previous timeout/retry policy. After response headers,
the ordinary stream timeout is restored, including PRO thinking. Accepted streams,
partial answers, tool continuations, cancellation, invalid requests and auth
failures never restart on another provider. No extra stored request or message
is created by a provider switch; the existing idempotency and generation locks
remain in force. A reserve uses a different model and does not promise identical
answer quality. No new project, service, paid fallback or model is provisioned.

`VELIA_VOICE_HEADER_TIMEOUT` and `VELIA_QUANTUM_RESPONSE_TIMING` identify provider,
public mode/upstream model and configured reserve count without prompt text,
audio or credentials. Validate live reserve availability after setting the key
and deploying. Local regression coverage uses real Requests/aiohttp loopback
servers to distinguish slow headers from a slow but accepted streamed body.

Existing account ownership, request idempotency, generation locks, guest 30-message trial, application quotas, and PRO token-access rules stay enforced. New saved-chat requests fail closed if the gateway is upgraded before a backend advertising the free tiers. PRO image uploads use local text/OCR extraction in this mode, rather than a paid vision model. General image understanding is not claimed.

The old Bonsai worker should be stopped only after the new chat and Browser Agent release checks succeed; this patch does not stop or delete it. PostgreSQL persistence tests are mandatory in the existing build CI. The existing live Flash predeploy probe now exercises the selected free API route; configuration checks ensure all three routes exist. Further live model/phone latency measurements remain necessary.


## Durable background chat tasks

Enable `VELIA_BACKGROUND_CHAT_ENABLED=true` on the existing account backend after deploying this source, with the pinned free API tiers enabled. The backend advertises `chat_background_tasks`; the gateway exposes controls only when that capability is present. No extra service, project, worker machine, paid model or fallback is created.

Choose Quantum or PRO; **В фоне** is enabled by default for text and voice requests. Acceptance returns a durable task ID immediately; the selected model runs on the backend while the UI switches subsequent conversation to Flash. The original conversation owns the request, attachments and final model-labelled answer. Polling is only an observer: leaving/reloading/closing the page does not cancel execution. The task card offers cancel, up to three idempotent clarifications, open result and explicit read-aloud. Completion refreshes the original chat without clearing unsent text/files, stopping the microphone, changing another conversation or interrupting speech. No browser push or external messaging is enabled.

One active task per account, 30 queued/running tasks globally, one background inference globally, and the existing single foreground provider lane bound API concurrency. Original application quotas reserve a zero-cost assistant row atomically; existing PRO credit access is checked on acceptance, revision and execution. Ordinary chat cannot silently revise the task. Flash receives owner/conversation-scoped task status and available result context, never hidden model reasoning. Task snapshots preserve roles, the current request by ID, bounded file/OCR context and the account language. Clarification invalidates the old revision; only the current lease and revision can publish. Cancellation takes effect in storage immediately and fences late results; an upstream request may finish before its connection can be closed.

Jobs use the existing PostgreSQL and a startup-managed loop in the account backend. Expired 600-second leases recover after a restart, with at most four total inference attempts per job, including revisions/recovery. A shutdown/restart may delay recovery until lease expiry. Completed/cancelled jobs discard their duplicate conversation snapshot; the answer stays in normal chat history. List polls return metadata; a single full result is fetched only when requested. All public routes authenticate ownership, hide deleted conversations and filter server metadata. The gateway rechecks same-origin mutations and preserves PRO access rules.

Plain background tasks produce text answers/research with the existing optional bounded web-search tool and file/OCR support. Explicit **Agent** tasks additionally use the existing browser executor as described below. A voice-originated task gets a full written result; spoken conversation with Flash stays short. Acceptance uses a prepared acknowledgement in supported account locales and otherwise resumes listening without inventing a translation. Read-aloud uses the existing selected/device voice. Provider free quotas, overload and device/browser speech limits remain.

Local verification includes durable SQLite store concurrency, disconnect/reconnect HTTP, ownership/idempotency/cancellation/revision fencing, gateway CSRF/access and simultaneous Flash routing, Node tests and full-app synthetic DOM acceptance. The PostgreSQL publication/foreground-concurrency test is included in the existing mandatory Docker Flash CI; it requires the dedicated test DB and is skipped locally when absent. Actual phone audio and live provider latency require release validation.


## Background browser Agent with selected-model planning and review

With the existing free API and background flags enabled, configure `VELIA_BACKGROUND_AGENT_ORIGIN` on the account backend to the HTTPS gateway origin. Set the same new `VELIA_BACKGROUND_AGENT_KEY` on those two existing services. It authenticates only the private bridge; no model keys, bearer sessions, cookies or browser credentials are stored in a task or exposed to the browser. The gateway keeps its existing Agent Core origin/key and executor unchanged. No extra service, database, paid model or paid fallback is added.

Select Quantum or PRO and enable **Agent**. This forces background execution; the chosen model produces a bounded brief, the existing browser Agent executes the original request, then the chosen model reviews the returned evidence. The composer returns to Flash without leaving the original conversation. Text and voice submissions use the same path. Ordinary chat does not silently authorize browser actions. Browser Agent retains its existing tool allowlist, browser session isolation, manual takeover and access controls. PRO retains the account credit access gate.

The task card shows planning, browser execution and review. A login/OTP/CAPTCHA/device handoff ends that execution as needing user input, with explicit browser and continuation controls. Continuation uses the authenticated owner's original job, model, conversation and stored scope, never a client-provided replacement goal. Parent-to-child linkage is atomic and repeated continuation requests resolve to the same child. Superseded handoff cards are hidden. The browser conversation identity stays the same.

An action-dispatch checkpoint is durable before the bridge POST. A restart or ambiguous network failure never automatically repeats that dispatch. A saved executor receipt can be reviewed again after a revision without repeating browser actions. Phase checkpoints renew the 600-second lease; cancellation/revision fences further steps and publication. Already-sent remote actions may still finish after cancellation or a disconnect; cancellation is not an undo. Revisions after execution cannot retroactively change completed actions. Receipt data is bounded and discarded after publication; if model review is unavailable, the unverified result is published with an explicit error and marker rather than being presented as checked.

The bridge accepts a fixed schema, authenticated server-to-server owner/session identity and a fixed server-configured target, with no redirects or POST retries. Its in-process duplicate-dispatch guard supplements the backend's durable checkpoint. Existing browser executor time and concurrency limits still apply. A task uses at most two selected-model calls plus the existing executor's bounded calls. Free-provider limits and existing hosting charges still apply.

Local validation uses selected-model orchestration mocks, real SQLite durability, private bridge HTTP authentication/ownership/replay checks and full-app synthetic DOM scenarios for PRO and Quantum, Flash conversation, handoff continuation, result restoration and card dismissal. The PostgreSQL atomic resume test runs in mandatory Docker CI and requires the dedicated test DB locally. No real user website action or live-provider latency was tested in local fixtures.
