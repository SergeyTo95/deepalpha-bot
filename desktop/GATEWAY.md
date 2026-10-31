# Isolated VELIA Desktop gateway

The Desktop service uses `Dockerfile.gateway` independently of the full VELIA backend. It runs no Telegram bot or payment workers. Existing VELIA device pairing and refresh are relayed only to the configured HTTPS identity authority. Desktop and signed-in Web requests verify the device token through that authority's `/mobile-api/v1/me`; there is no authentication cache or test-token bypass. Optional Web guests use a separate Flash-only route and persistent trial counters. Web Internet search also uses the existing preview PostgreSQL for opaque account-search references and public source facts; see [WEB.md](WEB.md).

Pairing/refresh responses are checked against the same account access policy before any tokens are returned. Without the public-access opt-in, sessions issued to accounts outside the preview allowlist are revoked. Identity and model redirects are refused. Browser cookies and upstream response headers are never relayed. Authentication payloads are limited to 16 KiB; unexpected fields are rejected. Authentication POSTs are limited to 60 per minute per process.

Browser pairing links redirect to the existing signed-in VELIA `/mobile-connect` page. The isolated gateway stores no account credentials. Electron's main process keeps device tokens encrypted; Harness receives only its local proxy key.

Required service variables:

```text
PORT=8080
VELIA_DESKTOP_API_ENABLED=true
VELIA_DESKTOP_PREVIEW_USER_IDS=<explicit owner Telegram id>
VELIA_DESKTOP_AUTH_ORIGIN=<existing VELIA HTTPS API origin>
VELIA_DESKTOP_BROWSER_ORIGIN=https://deepalpha-ai.com
KIMI_API_KEY=${{deepalpha-bot.KIMI_API_KEY}}
KIMI_BASE_URL=${{deepalpha-bot.KIMI_BASE_URL}}
VELIA_DESKTOP_PRO_MODEL=${{deepalpha-bot.KIMI_MODEL}}
VELIA_DESKTOP_REASONING_EFFORT=low
```

### Voice connection preparation

The voice dialog's microphone button pauses input without stopping an in-flight
answer or its playback. Late callbacks from the paused recognizer cannot submit
text, and the microphone resumes only after an explicit button press. Closing the
conversation resets this pause. Empty/punctuation-only recognition output and
explicit `[noise]`/`[music]`-style markers are ignored; actual short words, numbers,
negation and low-confidence transcripts are preserved. This is a transcript
guard, not acoustic classification, speaker identification or a wake-word mode.
Android browser recognition keeps its existing microphone source; no extra
capture/upload, speech provider, permission or voiceprint storage is introduced.

When a persisted answer replaces its streamed draft, speech is reconciled against
completed phrases and the current phrase, rather than the cursor that also
includes unsaid prefetched text. Matching phrases are retained, changed queued
audio is cancelled, and only the canonical remainder is spoken. A matching
phrase keeps its player and connection. Changed words (including negation and
amounts) are not treated as duplicates; a changed phrase must be corrected.
Already audible draft speech cannot be retracted. This adds no model call or
wait for the whole answer. Local speech callbacks are fenced by utterance identity
so a late end event cannot advance another phrase. Fetch/stream network errors
use a readable message while retaining received text, without automatic POST replay.

The browser Agent predeploy acceptance retries up to three times only for
502/503/504 or a connection timeout/failure. Every retry uses a fresh synthetic
session and repeats the two fixed read-only example.com checks. Invalid results,
authentication/quota failures and lost session continuity still block release.
This policy never retries real user Agent actions. No check is removed or skipped.

`VELIA_WEB_TTS_CHANNEL_ENABLED=true` additionally advertises one same-origin
`velia-voice-v1` WebSocket per answer. Its opening request validates the account;
each segment keeps the existing voice, size and per-user rate checks. Origin and
protocol checks, three pending segments, two channels per owner, sixteen global
channels and a three-minute maximum lifetime bound the channel. Closing or
correcting an answer cancels pending speech. Legacy HTTP remains the fallback
before audio starts; partially delivered audio is never replayed. The browser
prepares one or two next segments based on remaining playback and observed
delivery latency. Timing events contain only the model tier, clock type and
durations for recognition, first text and playback start relative to the browser's
speech-end event. They contain no speech text or audio.

`VELIA_WEB_TTS_REUSE_ENABLED=true` opts the existing Edge voice route into a
bounded pool of account-scoped WebSocket connections. Opening voice mode starts
authenticated, same-origin preparation in the background, without sending speech
text or making a synthesis request. The browser prepares and reuses its audio
element in that same opening gesture. The model, speaker, rate, SSML and MP3
format are unchanged. Idle sockets expire after 45 seconds; shutdown, interrupted
audio and mismatched request IDs close their sockets. Connection failures before
the first audio byte fall back to the original transport; partial audio is never
replayed. The option defaults off for a controlled rollout.

### Public account access configuration

Set `VELIA_PUBLIC_ACCESS_ENABLED=true` on the gateway to admit all accounts
verified by the existing identity authority, without listing Telegram IDs in
`VELIA_DESKTOP_PREVIEW_USER_IDS`. The flag defaults off; disabling it restores
the explicit preview list. Health accepts either public access or a preview list.
On the production identity backend, leave `VELIA_CHAT_ENABLED=true` and set
`VELIA_CHAT_BETA_USER_IDS` to an empty string to allow new accounts to obtain
their one-time Telegram sign-in code. Apply both service changes together.

Authentication, session revalidation, ownership, admin checks, model balance
requirements and request/usage limits remain enforced. This setting grants no
administrator or repository access and does not change guest access.

Use references within the existing PR environment; do not copy provider keys to the client. Authenticated accounts use the access policy above; optional Web guests have only the 30-request Flash trial. Shared model admission remains one concurrent request per identity, two per process and 30 per hour. PRO permits 4096 output tokens and requires positive real Credits; Flash permits 512 output tokens. The input cap is 1 MiB. Kimi K3 requests use an explicit low reasoning budget. Commercial debit tariffs and distributed active-generation locking are still outside this preview.

The build runs the three Desktop gateway/relay/Flash test files and the Web account and guest HTTP tests. The independent service's pre-deploy command is `python -m desktop.probe_gateway --live`. The PRO probe makes two bounded provider calls: a correlated test-tool exchange and an SSE answer. It also checks that existing device authentication is enabled and rejects requests without a token. When guests are enabled, private probe keys verify the persistent PostgreSQL limit under parallel reservations. The probe does not create an account session or establish real-owner pairing. It is not an HTTP endpoint.

## Flash in Desktop

The bundled composer offers only VELIA PRO and VELIA FLASH. The existing DeepSeek API/account adapters are disabled in this VELIA profile. Local first-message session titles avoid an auxiliary model request competing with the active tool loop. PRO remains the initial selection; changing the model does not make a provider request.

Enable Flash only in the isolated PR environment:

```text
VELIA_DESKTOP_FLASH_ENABLED=true
VELIA_DESKTOP_FLASH_BASE_URL=http://${{velia-bonsai-acceptance.RAILWAY_PRIVATE_DOMAIN}}:8080
VELIA_DESKTOP_FLASH_API_KEY=${{velia-bonsai-acceptance.VELIA_FLASH_API_KEY}}
VELIA_DESKTOP_FLASH_CONTEXT_TOKENS=8192
```

The existing cloned preview worker uses `desktop/Dockerfile.flash-worker` and `desktop/flash_worker_start.py`. Its model checksum and AVX2 runtime revision match the verified production image. The desktop-only start script uses `--reasoning auto`; each gateway request disables thinking with `reasoning_effort=none`. The production worker start script and Android Flash configuration are unchanged.

Flash has an 8192-token context and a 512-token answer cap. The gateway merges system/developer instructions into one leading message, normalizes function arguments, preserves tool-call correlation and removes prior reasoning from the next request. It renders and tokenizes the complete messages and tool definitions before generation, reserves space for the answer, and rejects overflow with `flash_context_too_long`. Nothing silently falls back to a paid provider. Flash receives up to 360 seconds; closing the local request cancels the model request.

The pinned pi-ai client reserves an additional 4096 tokens above its prompt estimate. With the full tool set and an 8192-token model it clamps the declared output budget to one token, even though the worker's exact tokenizer reports that the request fits. The Desktop loopback proxy restores the declared 512-token budget specifically for Flash tool requests clamped to one token. Other limits and PRO bodies are preserved. The gateway's exact context check includes this restored budget before inference.

When Flash is enabled, pre-deploy also checks a small real tool call and starts the exact shipped Harness JavaScript runtime against a private loopback gateway. A synthetic operator identity reads a newly created local `probe.txt`, returns its marker through the real `read` tool and verifies the final live answer. It declares the full shipped tool set; the test does not substitute a smaller agent. The synthetic allowlist exists only in that pre-deploy process and is restored on exit. It is unrelated to the owner account and is never an endpoint in the public gateway. Harness receives no provider or real account credentials. Failure blocks deployment.

`desktop/scripts/smoke-model-picker.mjs` checks the real Web composer: exactly two choices, Flash selection retained after reload, a switch back to PRO and zero model calls. Run with a local Playwright module and the extracted pinned runtime. It verifies the Web view, not native Windows GUI or owner pairing.

The existing full backend retains all its original build, Flash and production gates. Use a separate deployment branch with the gateway Dockerfile copied to root `Dockerfile`; keep that root override out of the Desktop feature PR. Apply the gateway healthcheck `/health` and settings only to the new preview service. A failed provider probe must block this gateway deployment.

The Windows app accepts `VELIA_GATEWAY_URL` and, in the gateway update, `VELIA_DESKTOP_HOME`. The small application-resource update and preview launcher reuse the existing executable and bundled runtime. The launcher selects a separate preview credential directory under `%LOCALAPPDATA%`, preserving the original account store. Close the existing app before updating. A credential store paired to another origin must not be reused silently.

Native Windows GUI/credential-store acceptance and pairing through the real owner's signed-in browser require the owner's machine. Do not report those as verified from synthetic HTTP identities or the provider probe.

### Voice input across iPhone, Android and desktop browsers

Voice mode feature-detects `SpeechRecognition` and Safari's
`webkitSpeechRecognition`. Native interim hypotheses are displayed in the call
window; only the final complete turn is submitted. Cancelling or muting clears
the display and invalidates late recognition callbacks.

If `GROQ_API_KEY` and the existing explicit free-account declaration
`VELIA_VOICE_GROQ_FREE_ACCOUNT=true` are configured, the authenticated voice
catalog advertises an optional recording fallback. No new paid provider or plan
is enabled. The UI explains that a recording is sent to Groq before the user
presses **Записать реплику**. The second press submits the complete utterance.
After the reply, recording again requires a press; this avoids continuously
sending background audio. It also works when native browser recognition is
missing or unavailable.

The recorder negotiates MP4/AAC (Safari), WebM/Opus or Ogg/Opus at runtime;
requests echo cancellation, noise suppression and automatic gain control where
supported; and releases microphone tracks immediately when stopped, cancelled,
muted or hidden. Recordings remain in memory and are not logged or stored by the
gateway. Groq's data handling still applies. Uploads are limited to 1 MiB and
recording to 45 seconds; exceeding either rejects the turn rather than sending a
truncated phrase. The server requires a signed-in same-origin request, validates
the container signature, allows two concurrent uploads and eight attempts per
user/minute, and uses only the fixed `whisper-large-v3-turbo` endpoint. Provider
429 responses stop the request without switching to a paid service. Free
provider quotas are shared with other requests using that account.

Speech-only Russian normalization expands common abbreviations, valid ISO dates
and ruble amounts without changing chat text or phrase reconciliation identities.
Sentence segmentation avoids pauses inside common abbreviations and initials.
`VELIA_ACCOUNT_RESPONSE_TIMING` additionally reports `auth_ms` and `admission_ms`,
and `VELIA_VOICE_STT` records duration only. No transcript/audio is included in
these diagnostics.

Validation: Node tests cover Safari's prefixed API, native interim revisions,
three recording containers, exactly-once submission, mute/close races, late
microphone permission, recording/upload bounds and speech normalization. Server
tests cover auth/origin, free-account gating, fixed model/URL, MIME/signatures,
limits and cancellation. These tests use synthetic hardware; physical iPhone,
Android, macOS and Windows microphone/playback testing remains necessary. Check
that microphone permission, one short Russian phrase, a second turn, closing
while recording, backgrounding the tab and resuming audio work on each device.
Unsupported browsers offer keyboard dictation/text instead of claiming full
voice support. Neural VAD, RNNoise, sound classification and offline ASR are not
part of this release; this fallback uses the browser's own capture processing.

### Voice pauses and numeric finalization

A canonical update that changes `215000` to `215 000` (including non-breaking
or narrow spaces) now retains matching spoken phrases and the active player /
answer channel. Reconciliation compares validated thousands-group whitespace
only and maps the match back to the exact canonical tail. Changed digits, minus
signs, decimal separators and negation still require correction. A spoken result
is not replayed merely because its numeric formatting changes. The regression
was reproduced for both completed and in-flight calculation audio before this fix.

The sentence boundary detector waits when a numeric full stop is the last
available streaming character, because it may still become a decimal point.
Final completion flushes that text immediately. Russian `н.э.` and `т.к.` do not
split a spoken clause; a split `н.` prefix is also retained.

Authenticated `voice_flow` diagnostics report a single bounded summary per voice
turn: model, outcome, audible segment count, canonical correction count, active
interruptions, recoverable playback pauses, maximum gap from a completed segment
to the next audible segment, and maximum time from requesting playback to its
first audible progress. They contain no text, audio, account IDs, phrase hashes
or provider credentials, and retain the existing event rate limit. These numbers
separate model/transport waiting from client-side interruptions; they do not
claim to measure physical speaker output. Real phone tests are still needed.

The 2026-10-31 15:38 Istanbul incident had one successful Flash model call:
provider first text about 6.0 s, gateway first text about 6.9 s, browser first text
7.4 s, browser first audio 9.8 s, gateway completion about 16.4 s. Runtime logs
alone do not establish that the particular utterance replayed or reveal the gap
between its spoken parts. Synthetic regression tests cover the formatting replay
case, preservation of one remote channel, decimal streaming boundaries and
content-free gap diagnostics.
