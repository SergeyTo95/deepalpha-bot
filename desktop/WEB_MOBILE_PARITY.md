# Browser/mobile feature audit — 2026-10-07

Compared the Android `develop` API and screens with the deployed gateway branch
`ci/velia-desktop-gateway-railway`. The Android default `main` is only a README;
the comparison therefore uses the actual app branch.

| Area | Browser implementation |
| --- | --- |
| Model picker | Flash and existing balance-gated PRO; disabled Quantum with «Скоро · в разработке». Quantum is not an inference ID. |
| Chat | Existing account history and streaming; attachments, rename, pin, public snapshot link, copy and read aloud. |
| Studio | Image provider capabilities, Image 2 when enabled, references, transparency, video durations from status, image-to-video, music/lyrics, sessions, results and downloads. |
| Projects | Passport creation/edit with expected revision, project resource creation and listing. |
| Research | Missions, literature, synthesis, bounded autonomy, reports, sources, claims, evidence and alerts. |
| Medical | Contrast/abdomen confirmations, cases, DICOM ZIP/NIfTI, resumable 8 MiB chunks with SHA-256, completion, result refresh and research creation. |
| Agents | Capability selection, agent creation/list/delete, child conversation creation. |
| Autopilot | Repository selection, bounded missions, activate/pause, enqueue/cancel tasks, run history and CI/review/merge-policy reads. |
| Account | Shared personalization, plugin switches, credits and usage. |
| Voice | User-started browser recognition, language/voice settings, voice dialog and answer playback; answers remain in chat. |

Native offline voice packs, Android Bluetooth/device control, Android background
services and Google Play billing do not transfer to browser APIs. Browser voice
support depends on the browser/device. Physical microphone, acoustic quality and
actual GPU image/video/music/CT inference were not exercised by synthetic QA.

## Security and validation

- Every feature request resolves the existing encrypted, HttpOnly Web session
  and rechecks the account against the identity authority.
- Mutations require same-origin headers; the relay accepts only enumerated
  mobile paths/methods. It cannot relay auth/admin or arbitrary URLs.
- Signed media paths are constrained, with matching account IDs; redirects,
  authority cookies and provider credentials are not forwarded to clients.
- Uploads are bounded, JSON endpoints retain their existing limits, and
  idempotency keys are carried to the authority.
- Gateway build suite: 362 Python tests passed. Core browser tests: 10 passed.
- `desktop/scripts/smoke-web-features.mjs`: real Chromium, synthetic feature
  responses; ten sections, disabled Quantum, Image 2 payload, 15-second video
  payload, canonical autopilot mission IDs, desktop/mobile layouts and no JS
  exceptions. This proves adapter/UI behavior, not live media inference.
- The older `smoke-web-chat.mjs` fixture does not implement the current
  request-intent JSON response and times out expecting sources on its guest
  greeting. That fixture failure is not counted as passing acceptance.

The changes target only the separate browser gateway deployment branch.

## Workspace design verification

Shared cards, form fields, inline checkbox rows, primary actions, grouped navigation, active section indicators and Studio mode buttons support both themes. The sidebar uses one scroll surface to keep all navigation items reachable without clipping on short phone screens. The model list remains openable with Agent enabled and indicates its Flash requirement. Chromium acceptance covers all ten sections, 360/390/768/1440 px widths, no horizontal overflow, checkbox label clicks and selected navigation state. Feature responses in this UI check are synthetic.

Short-screen regression checks cover 393 px width with 640/710/844 px height, model picker bounds in Agent mode and navigation/history separation. The Agent capability flag is synthetic in this UI check; no agent request is sent.

## Autopilot task center

Autopilot now separates scheduled task templates from GitHub development missions. Authenticated schedule routes are explicitly allowlisted in the gateway. The UI checks scheduler and Agent Core availability before offering creation, starts schedules paused, supports daily/weekly/hourly intervals with an IANA timezone, displays the latest job result and explicit approval/run controls. Only server-advertised built-in task tools and connected calendar reads are offered. Chat text may prefill a task draft; it is not a replay of an arbitrary browser instruction. No server worker flags are enabled by this change, and background browser automation or notifications are not implemented.

Validation: 45 focused Python tests, 10 Node tests, and synthetic Chromium checks for weekly payloads, enable/pause, disabled scheduler, approval-before-run, GitHub mission compatibility and mobile layouts. This does not qualify actual production scheduled execution.

## File and answer utilities

Added a local image viewer with zoom, bounded plain-text preview/copy, native PDF open/download, and a clear DOCX preview limitation. Studio image results use the same viewer. Files may be selected, dropped or pasted; selection enforces four files and 15 MiB each. MD/CSV/JSON normalize to the backend-supported text/plain MIME without changing bytes; XLSX/SVG are rejected. Quick attachment prompts fill the composer without sending automatically. Answers and conversations can be downloaded as Markdown.

Original attachment previews are session-memory only (12 files / 60 MiB bounded cache), cleared on account changes/logout; object URLs are revoked when viewers close. Restored history receives allowlisted attachment metadata, never original bytes, extracted text, private URLs or credentials. Originals cannot be reopened after reload unless reselected. DOCX analysis and image understanding continue through the existing backend; this change does not qualify real model inference, PDF OCR or GPU media generation.

Verification: 36 focused server tests, 13 Node tests and synthetic Chromium checks for escaped text previews, image zoom, PDF links, file limits, unsupported formats, Studio viewer and answer/chat downloads.

## Browser voice conversation

### Progressive audio and playback status (2026-10-29)

Online playback shows “Готовлю голос…” while waiting for synthesis or browser
buffering. “Велия говорит…” starts only on the audio `playing` event, or the
device utterance `start` event. Waiting and stale events cannot leave the call
claiming speech after it has stopped.

The existing authenticated POST speech endpoint accepts `?stream=1` for the
configured Edge speakers. It forwards the first audio bytes without retaining
the entire phrase, then streams bounded MP3 chunks. Authentication, same-origin
checks, voice allowlists, per-user limits, four synthesis slots, the 2 MiB audio
bound and the 20-second provider deadline still apply. A disconnect cancels the
upstream generator; an error after the first audio byte closes the incomplete
HTTP stream, without retrying or switching speakers. The first provider audio
must arrive before HTTP 200 is committed. There are no new providers or keys.

Browsers supporting MediaSource `audio/mpeg` decode chunks progressively; others
retain Blob playback. The selected voice and one-phrase prefetch are preserved.
Tests cover early delivery, serial appends, cancellation, partial errors, auth,
slot release and serving the new web module. The real Chromium smoke uses a
synthetic MP3 tone and proves playback before provider EOF, status ordering,
microphone restart and Blob compatibility. It does not measure physical-device
speech or eliminate an upstream service's delay before its first audio bytes.

### Recognition turn completion (2026-10-29)

Recognition now allows a one-second pause before requesting stop and cancels
that timer while speech resumes or an interim hypothesis remains. It keeps the
recognizer attached until `end`, including final words delivered asynchronously
after `stop()`. A two-second missing-end watchdog aborts and asks for a repeat;
it never silently submits a potentially truncated prefix. Closing the call,
changing language and permission failures invalidate pending callbacks. The
recognizer remains the browser's Web Speech engine; these changes do not add
an audio-upload provider or guarantee recognition accuracy. The selected call
language still overrides the device locale and remains available for all
existing language choices. Eight deterministic lifecycle tests cover the pause
and late-final regressions. All 63 web tests and 22 voice-route Python tests
passed locally. The mobile synthetic smoke was updated for late final words,
but could not run in this environment: Chromium was absent and its download
returned an invalid archive. Physical microphone accuracy requires a device check.

Rebuilt the native Web Speech conversation loop with interim captions, distinct listening/thinking/speaking states, pause/resume, retry after silence, explicit permission/network/microphone errors and stale-callback guards. Pausing, closing, hiding the page or changing conversations stops microphone and speech. Rejected/no-op chat submissions return an actionable state instead of waiting forever; browser Agent replies now use the same voice completion hook.

Speech output splits long answers into bounded chunks and removes spoken code/URLs/Markdown while preserving the original chat reply. A speech watchdog stops a stalled engine. Settings expose conversation language, available device voices, rate and a sample; voices loaded asynchronously refresh the selector. Responses remain in normal conversation history. Recognition listens between spoken answers, so automatic barge-in/echo cancellation and real-time model audio are not claimed. TTS begins after the full model reply arrives.

Verification: 16 Node checks, focused gateway tests and synthetic Chromium speech lifecycle checks for interim captions, multi-chunk completion, silence retry, stale callbacks after pause, permission denial, selected voice/rate and mobile dialog bounds. Synthetic speech APIs qualify UI/control flow, not real microphone accuracy, actual speech quality or physical-device audio.

## Browser Agent capacity feedback

The gateway passes only allowlisted storage/profile-capacity error codes to the browser. Users receive a clear temporary-capacity message or an instruction to start a new Agent conversation when that profile reaches its limit; arbitrary upstream details are still sanitized. Browser-server cache sweeps, reserve checks and budgets are implemented separately on the Agent service branch. Existing browser user state is not deleted to admit a new request.

Verification: 39 focused gateway tests, including both capacity codes and sanitization of an arbitrary upstream error; 16 Node tests. Agent-server storage and session validation adds 22 tests.

### Voice conversation routing and Android profile parity (2026-10-07)
Voice replies use Flash's ordinary conversation route even when Browser Agent was previously enabled; the visible Agent toggle switches off and an account conversation retains both turns. Voice requests carry a validated boolean `voice_turn` through the account gateway to the existing mobile fast path. Direct/guest Flash voice requests use 128 output tokens and a short spoken persona. Automatic web planning/search is skipped for voice conversation; current-information research remains available in ordinary chat.

The web selector mirrors Android `VoicePreferences` profiles Velia/Auren/Nora/Mira/Sofia/Alina/Luna (voice slot, rate multiplier and pitch), supports an explicit device voice override, and persists the profile. These are system-TTS profiles, not seven independently hosted voice models, and their actual timbre depends on the browser/device voice inventory. Android's offline ASR models are not loaded by this web adapter.

Starting explicitly primes browser TTS in the button gesture; a sound-test button is available inside the call. A final recognition result stops capture promptly. The ordinary generation has a 90-second voice deadline and reports failure instead of leaving the call pending indefinitely. Local acceptance: 41 gateway tests, 17 Node tests, synthetic speech lifecycle checks, and full web-app fixture testing of Agent ON → voice Flash → audible utterance submission → resumed microphone → second reply in the same stored conversation. Synthetic APIs do not qualify actual microphone recognition, audio hardware or timbre on a user's phone.

### Streaming speech and real voice inventory (2026-10-07, supersedes profile selector above)
The call now selects actual browser `SpeechSynthesisVoice.voiceURI` values scoped to its language and deduplicated by URI. The seven Android-style aliases were removed because multiple profiles could map to the same installed voice; pitch/rate variants do not establish different speakers. The UI explains when only one voice is exposed. Genuine speaker variety remains dependent on the device/browser inventory; no hosted multi-speaker engine was added.

Voice-mode stream updates release completed phrases immediately, enqueue remaining phrases while speech is playing, flush the tail on final completion, and resume recognition only after final text and speech queue completion. Final canonical text does not replay already queued phrases. A canonical rewrite of spoken text stops the old queue and directs the user to the saved answer. Fenced code waits for completion before sanitization. Emoji, modifiers, joiners, keycaps and flags are removed from spoken text only. Full-app synthetic acceptance now asserts the first speech submission occurs while generation is still busy. This measures control-flow ordering, not real device latency or actual audible timbre.

## Quantum voice transport and deployment isolation

The browser gateway and account backend must both use the same explicitly
selected canonical model configuration. A successful account-backend deployment does
not update the separate browser service. An older gateway that still pins the
obsolete `:free` alias can reject a voice preparation request before the account
backend receives it.

For recurring gateway deployment use
`python -m desktop.probe_quantum_independence --config-only` followed by the
existing Browser Agent acceptance probe. Configuration-only checks make no
provider/voice synthesis requests and do not claim live Quantum availability.
Keep `--access` and `--voice` as explicit operator diagnostics; their failure
remains a failure. Runtime inference still verifies the exact key's hard $0
spending cap on each request and never falls back to a paid model or another
model's credentials.

Self-contained greetings with the assistant name (including the bounded ASR
variants «Виля» and «Веля») skip redundant interpretation while preserving the
original transcript and selected answering model. Factual, medical, current
information and mixed requests retain the existing interpretation/retrieval
path. Monetary key-cap errors have a separate public message and code; they are
not represented as a consumed daily message allowance.

Automated transport tests cover greeting transcripts, preservation of prior
message content, and routing to the selected model. Physical microphone quality,
live provider quota, TTS availability, and full browser history after a failed
voice turn still require browser/device acceptance.

## Explicit free-core Quantum migration

Quantum can explicitly select `deepseek-v4.1-flash` using the same dedicated
Experiential credentials. Both gateway preparation and account generation must
use that model; there is no automatic model fallback. The canonical GLM model
remains supported for compatibility but its public catalog currently lists paid
rates, not a free allowance. The key's verified hard $0 cap remains mandatory
on every call. Core Free pricing is account policy, not a claim that the public
list price is zero; the published limits are shared across the organization.

Run `python -m velia_quantum_provider` once as a migration acceptance gate in
each service environment. It makes two synthetic requests under that service's
key and checks a correct tool result, a non-empty completed text stream, actual
usage tokens, explicit reported cost of zero, and no BYOK billing. Any missing
evidence or provider refusal fails acceptance without raising the key cap.
It does not establish physical microphone or TTS quality. After acceptance,
return the recurring deploy command to its existing offline Quantum check.

## Multilingual integration audit (2026-10-27)

Text chat has no language allowlist. The system asks Quantum to answer in the
user's language; Unicode text and history are forwarded without translating to
Russian. Test prompts in three languages are samples, not a support boundary.
This transport property does not establish semantic quality in every language.

Voice selection now includes ISO 639-1 languages plus canonical BCP 47 locales
reported by installed voices, navigator.languages, and the saved selection. The
initial default follows the device before the Russian page language. This is
approximately 190 language/region choices on the standard fixture, not a claim
that the microphone recognizer or installed synthesizer supports all of them.
The hosted voice inventory remains limited to its declared languages; other
languages rely on the device. OCR readiness currently covers eng/rus/tur only.

The first canonical DeepSeek migration was refused by the actual backend key
with 429 insufficient_quota / key_daily_cap. Public free-tier advertising alone
therefore did not establish free access for this account. The prepared route
`deepseek-v4.1-flash:free` explicitly requests the provider's documented free-only
lane. The hard $0 key check and zero-cost live acceptance stay mandatory; the
absence of a free alias in model discovery alone does not prove that a documented
suffix is invalid. No paid retry or automatic model replacement is allowed.
https://platform.experientiallabs.ai/docs/errors

## Direct Z.ai Quantum migration (2026-10-27)

The actual Experiential `:free` request was refused with 403 model_not_granted;
this account's catalog offers DeepSeek free only with the paid Pro plan.
The next explicit Quantum migration uses Z.ai Standard API, independently of
Experiential. Configure both the account backend and browser gateway:

```
VELIA_QUANTUM_ENABLED=true
VELIA_QUANTUM_PROVIDER=zai
VELIA_QUANTUM_BASE_URL=https://api.z.ai/api/paas/v4
VELIA_QUANTUM_MODEL=glm-4.7-flash
VELIA_QUANTUM_API_KEY=<dedicated Z.ai Standard API key>
```

Only this published zero-priced model and exact endpoint are accepted for Z.ai.
The key is supplied by the operator in Railway, never committed to source.
No EXPLABS/Kimi credential fallback, Coding Plan endpoint, FlashX or paid GLM
fallback is allowed. Built-in billed tools are rejected; only local function
descriptions may be passed. Thinking is disabled to avoid hidden reasoning
latency. Z.ai documents tool_choice=auto; the caller still requires and validates
the function result, so a plain text planner response fails closed.

Official API and current pricing:
https://docs.z.ai/guides/overview/quick-start
https://docs.z.ai/guides/overview/pricing
https://docs.z.ai/api-reference/llm/chat-completion

`python -m velia_quantum_provider` checks one synthetic function result and a
completed stream against the service's actual key. For Z.ai it records the
published-free pricing basis, not a fictitious metered dollar cost or an
Experiential cap check. There is no documented model catalog check here; the
gateway's live planning probe verifies real requests rather than key shape.
After migration acceptance restore offline Quantum checks on recurring deploys.
Account quota, rate limits, real multilingual quality and physical voice
acceptance remain to be verified after the key is installed and deployment passes.

## Native Quantum tool turns (2026-10-28)

An account backend advertising `chat_quantum_native_tools` owns the whole
Quantum turn. The gateway forwards the original content, voice flag, attachment
IDs and explicit web_search boolean; it does not retrieve preparation history,
restore context metadata or call understand_request first. Existing backend
history, attachment extraction, permissions, limits and persistence still apply.
During a rolling release, older backends retain the qualified legacy route.
Deploy the account backend before the gateway.

`velia_quantum_turn.Turn` is shared by the native account generator and signed-in
local browser chats. The first streaming completion may answer directly or
call the local web_search function. Only a validated search call executes;
query/scope are bounded, arbitrary functions and repeated tool loops fail closed.
One search result permits exactly one model continuation with no further tools.
The same selected Quantum model/key is used for both completions. No retries,
paid model fallback or provider-built-in paid search is enabled.

The account service uses its existing WEB_SEARCH_PROVIDER/WEB_SEARCH_API_KEY;
local browser chats use the gateway's existing search service. Searches use
fixed provider endpoints with redirects disabled, bounded responses, public
source URLs, three snippets maximum and official domains for medical scope.
Sources are appended as clickable links to the saved answer so they survive
history reload, copying and export. Local browser turns also emit source cards.
The voice reader keeps source captions and strips Markdown URLs.

Z.ai business codes are logged as fixed codes and categories, never its message
or account details. 1302 is a request rate limit, 1305 overload, 1113 a balance/
package problem, and 1308/1310 usage limits. Unknown 429s are not labelled a
confirmed daily/free quota exhaustion. Original failures without these codes
cannot be retroactively classified. See https://docs.z.ai/api-reference/api-code .

Automated tests exercise direct/voice/search turns, original-history preservation,
one-versus-two completion counts, search failure, unsafe tool arguments, private
URL filtering, medical domains, Internet disabled and safe 429 diagnostics.
Physical microphone quality, actual model tool-choice quality and live API
availability still require service/device acceptance after deployment.

## Future self-hosting

Official checkpoint: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
Inspected revision: 314113f8a7dadb388215396ba5fbee2c610a04d6.
The 48 safetensors files total 510,296,708,312 bytes (approximately 510 GB).
The model card applies the MIT license to code and weights; preserve its notices.
Downloading is free of a model license fee, but storage and inference are not.
The model is a very large MoE: active parameters per token are not its total
storage or memory requirement. Official inference includes a TP8 reference.
vLLM also documents host offload for the large Engram tables:
https://docs.vllm.ai/en/latest/features/engram/

The existing self_hosted configuration accepts an owned OpenAI-compatible
endpoint, model ID, and dedicated token for both gateway and account backend.
That permits moving Quantum to our hardware without changing the public mode
name. It does not imply that today's provider alias identifies a byte-for-byte
copy of a particular public checkpoint. No weights were downloaded in this audit.

## Z.ai overload recovery (2026-10-28)

Saved account chat and native browser Quantum share bounded overload handling:
only an explicit HTTP 429 with business code 1305 before the first completion
may be retried once after 1–2 seconds. Responses close before waiting. No retry
is made for quota/authentication errors, network errors, in-stream failures or
a completion after executing web search. The model, body and dedicated key do
not change. Standard User-Agent/Accept headers are explicit.

Two actual 1305 rejections open a 30-second process-local cooldown scoped to
endpoint/model/key; other keys are unaffected. A subsequent successful HTTP
response clears it. This is not a distributed capacity guarantee across workers
or replicas. No independent backup provider is enabled by this release.

The account backend includes the explicit operator diagnostic
`python ci/probe-velia-quantum.py --compare-requests`: two bounded synthetic
calls, minimal then native-tool payload, with no retries, actual searches, user
history, model output or credentials in logs. It accepts only the pinned free
Z.ai model and reports provider unavailability without treating it as a healthy
model response. It is not part of recurring release readiness checks.

## Independent Groq reserve (2026-10-28)

Native account and signed-in browser Quantum support an explicit Groq backup:
`VELIA_QUANTUM_GROQ_BACKUP_ENABLED=true` plus dedicated `GROQ_API_KEY`.
Only `https://api.groq.com/openai/v1` and `openai/gpt-oss-120b` are allowed;
provider/model endpoint overrides and another provider's credentials are not
used. `max_completion_tokens` defaults to a maximum of 2048, reasoning effort
is low, and `include_reasoning=false`. No provider-hosted tools are enabled.
The existing custom search, safe tool limits, account budgets and history stay
in effect. There is no language allowlist added by the Groq adapter.

Only an explicit pre-response Z.ai 429/1302 or 429/1305, or its active overload
cooldown, may enter this backup. One Z.ai overload retry is retained. Auth,
balance, policy and quota errors, network errors, and every in-stream failure
stop without switching. After a backup starts, its search continuation stays
on Groq, with no replay of text or tools. The backend records fallback_used.

Groq Free is an account tier, not a universally zero-priced model or an API
key with an enforceable monetary cap. The operator must keep this account on
Free to avoid usage charges; this integration never upgrades billing or buys
credits, and cannot infer the account's billing plan from a successful model
response. Existing project daily limits are not increased by this release.

Explicit one-off acceptance: `python -m velia_quantum_groq_probe`. It verifies
key/model visibility, then two bounded streaming completions with one synthetic
function result. It does not execute real web search, log model output or keys,
or claim production language quality or physical voice acceptance. A failed
probe returns nonzero. Remove this live check from pre-deploy after acceptance;
recurring checks remain configuration-only for Quantum.

## Reproducible qualification runtime (2026-10-28)

The gateway no longer downloads its CLI from a transient Windows-artifact
service. The image installs the official published `@deepseek-ai/dsh`
`0.2.1-alpha.1`, with the root tarball and all transitive artifacts pinned by
`desktop/qualification-runtime/package-lock.json` integrity hashes. Install
scripts are disabled. Both qualification and final image run the CLI version
check; the explicit operator probe uses its new package path. This attests the
published package and lock, not the old Windows archive or an inferred Git SHA.
Node web tests, the Python gateway suite, Groq acceptance and the private remote
Browser Agent live check remain required.
