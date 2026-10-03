# VELIA Web Preview

The browser chat uses the existing isolated VELIA gateway and existing Flash
worker. It adds no model copy, database service, paid fallback, or public provider key.
Production Android, the Telegram bot and the production backend stay unchanged.

Enable only on the isolated preview gateway:

```text
VELIA_WEB_ENABLED=true
VELIA_WEB_ORIGIN=https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app
VELIA_WEB_SESSION_KEY=<Fernet.generate_key(), stable across deployments>
```

The root serves the responsive monochrome chat. The Web sign-in dialog opens
the existing Telegram `velia_connect` pairing flow directly, keeps the input
step open and focuses it when the browser regains focus. The explicit
“Код уже есть — ввести” button also focuses the field. Pending sign-in resumes
after a page reload. `/mobile-connect` remains the existing Desktop/WebApp path.
Authenticated preview accounts remain limited to the explicit owner allowlist.

Guest access is enabled on the same isolated preview gateway with:

```text
VELIA_WEB_GUEST_ENABLED=true
VELIA_WEB_GUEST_DATABASE_URL=${{Postgres.DATABASE_URL}}
VELIA_WEB_GUEST_TRUST_RAILWAY_IP=true
```

Visitors can use Flash without registration, for 30 admitted model requests
per guest browser. The remaining allowance appears below the composer. At zero,
the browser offers sign-in; PRO always requires an authenticated account with
positive Credits. Guest history is local and isolated from account history;
signing out restores the browser's guest history. No guest credentials can read
account conversations or use the raw Desktop endpoint.

The encrypted Secure, HttpOnly, SameSite=Lax `__Host-velia-guest` cookie lasts
365 days and uses the existing stable session key. The primary quota has no
automatic replenishment. The existing preview PostgreSQL stores only hashed
quota keys and bounded integer counters in `velia_web_guest_usage`, so server
restarts do not reset the allowance. A separate 30-request-per-UTC-day network
guard limits cookie clearing; shared networks share that guard. Only Railway's
`X-Real-IP` header is trusted with the explicit flag above; arbitrary forwarded
headers are ignored. Database transactions lock both rows and reserve quota
atomically. Invalid input and context overflow do not spend the allowance;
admitted attempts, including stopped or failed provider calls, do. Database
outages deny guest generation. The trial is a browser/network allowance, not a
verified per-person identity limit.

## Internet search

Enable VELIA_WEB_SEARCH_ENABLED=true on the existing preview gateway.
VELIA_WEB_SEARCH_PROVIDER and VELIA_WEB_SEARCH_API_KEY reference the existing
deepalpha-bot WEB_SEARCH_PROVIDER and WEB_SEARCH_API_KEY variables. Credentials
are never sent to the browser or model.

The Internet button is off by default. Turning it on enriches that message in
both Flash and PRO; changing the button itself makes no search/model request.
Search sends only the latest question (up to 50 words / 400 characters), makes
one bounded gateway provider request and returns up to three public snippets.
Tavily basic, Serper, Brave and the legacy Bing protocol are supported. The
selected provider must be configured and pass live qualification. No crawler,
arbitrary-URL fetcher, paid model fallback or extra model generation is added.

Search HTTP redirects are refused, responses are capped at 256 KiB, and the
deadline is 15 seconds. Nonpublic links and unsupported browser payload fields
are excluded. Sources are untrusted data and cannot grant tool access. Source
cards use retrieved URLs and a separate sanitized SSE event; they survive history
reload. Failed or empty search returns an explicit error before generation.
Ordinary chat remains usable.

Guests reserve one of their 30 requests before contacting the search provider;
an admitted search attempt also counts if search fails or is stopped. The base
Flash context is checked first, then the enriched context is checked again.
Overflow rejects the request without truncating the question or switching models.
Account PRO authorization still precedes search.

Account Internet messages use the existing authenticated conversation sender.
Their upstream message includes a bounded public evidence block for model
context. Web history restores the original question and separate source cards
using the existing preview PostgreSQL's velia_web_search_context table. Only
HMAC identifiers, original-question lengths and public search facts are stored
there; no account token, user ID or question plaintext is stored in this table.
Other clients may display the native message's evidence block. Replaying the
same account request uses its recorded evidence and retrieval time without
another gateway search. The native backend retains its existing plugin behavior.

Pre-deploy performs a real search that must return the official Python website,
qualifies PostgreSQL evidence persistence using private probe keys, then runs the
existing guest-quota, provider and shipped Harness gates. Failure blocks
deployment; mock browser results do not establish live search acceptance.

Account-backed VELIA FLASH and VELIA PRO stream through the existing mobile
conversation API and share gateway admission counters with Desktop: one concurrent request per user, two per process and 30
calls/hour. Flash retains the 8192-token context and 512-token answer limit;
PRO retains 4096 output tokens. Context overflow is an explicit error. The
browser never automatically switches Flash to the paid mode. Mode changes make
no model request. The browser chat executes no terminal/file tools.

Device tokens are encrypted with Fernet in a Secure, HttpOnly, SameSite=Lax
`__Host-velia-web` cookie. JavaScript receives only a public profile. The stable
key lets the cookie survive a server restart or sleep. Each request still
verifies the device against the existing identity authority. Refresh rotation
is serialized per session in the one-replica gateway; refreshed credentials
are set before SSE headers are sent. Keep one replica until rotation locking
is distributed. Logout revokes the device upstream and removes the browser
cookie. POSTs require the exact configured Origin and a custom same-origin
header. No CORS origin is opened. Raw provider metadata and reasoning are
excluded from browser SSE events. Raw HTML in messages is rendered as text.

The browser fetches the signed-in account's existing conversations (up to 100)
and messages (latest 200), creates new conversations upstream, streams through
`messages/stream` and deletes through the same authenticated store. Android and
Web therefore share history and server context. Existing browser-only chats
are retained as local entries; their deletion only affects that browser. The
local cache and active selection remain isolated by account. Usage, internal
plans, provider metadata and credentials are stripped from upstream responses.
SSE resets and idempotent duplicate completions use the canonical persisted
answer; stopping the browser stream does not erase the backend conversation.

Flash is the default. PRO checks `/mobile-api/v1/economy/me` on every request,
including the raw Desktop endpoint; zero or negative Credits returns 402 before
any paid provider call. Unknown/unavailable balance fails closed for PRO while
Flash remains usable. The UI shows Credits and disables PRO at zero. There are
no owner or tester exemptions. This change adds eligibility checking only; it
does not mint tokens, enable purchases or invent a debit tariff. Existing
backend accounting/budgets remain in force. Commercial usage charging before
broad paid launch remains separate from this positive-balance access condition.

Validation: the gateway Docker build runs the focused Python gateway/Web HTTP
tests and Web stream/Markdown unit tests. `scripts/smoke-web-chat.mjs` starts its
own loopback-only synthetic authority and model, then checks real browser login,
guest Flash and persistent remaining allowance, Flash/PRO routing, reload/history,
stop, deletion/search, logout, theme and mobile
navigation. It needs Playwright and a Chromium binary; module/executable paths
can be supplied with `VELIA_PLAYWRIGHT_MODULE`, `VELIA_CHROMIUM_MODULE` and
`VELIA_CHROMIUM_EXECUTABLE`. `serve-web-fixture.py` is never copied into the
public image. Browser fixtures do not establish real-owner pairing or live
model acceptance. The existing `python -m desktop.probe_gateway --live`
pre-deploy gate still checks real providers and the actual shipped Harness.
With guest access enabled, it also checks the actual PostgreSQL using private
random probe keys: 30 concurrent reservations succeed, excess reservations fail,
and a fresh store sees the exhausted quota. Only those probe rows are removed.

## User decisions — 2026-10-03

- Web chat is the current priority, with both Flash and PRO on the included
  Railway address. The initial design is monochrome, with dark and light themes.
- The owner has not downloaded the Windows Desktop bundle yet.
- macOS packaging is deferred; no accepted Mac artifact is available.
- The owner plans to buy a domain for Velyon Core later (entered as «Велорин кор»
  in the conversation). No exact hostname has been provided or purchased here.
  Keep VELIA / Velyon Core as the public product branding. Attach the owned
  domain and update VELIA_WEB_ORIGIN when the actual hostname is provided.
