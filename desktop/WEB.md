# VELIA Web Preview

The browser chat uses the existing isolated VELIA gateway and existing Flash
worker. It adds no model copy, database, paid fallback, or public provider key.
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
after a page reload. `/mobile-connect` remains the existing Desktop/WebApp path. Access remains limited to the
explicit owner allowlist. Publicly accessible HTML does not grant model access.

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
Flash/PRO routing, reload/history, stop, deletion/search, logout, theme and mobile
navigation. It needs Playwright and a Chromium binary; module/executable paths
can be supplied with `VELIA_PLAYWRIGHT_MODULE`, `VELIA_CHROMIUM_MODULE` and
`VELIA_CHROMIUM_EXECUTABLE`. `serve-web-fixture.py` is never copied into the
public image. Browser fixtures do not establish real-owner pairing or live
model acceptance. The existing `python -m desktop.probe_gateway --live`
pre-deploy gate still checks real providers and the actual shipped Harness.

## User decisions — 2026-10-03

- Web chat is the current priority, with both Flash and PRO on the included
  Railway address. The initial design is monochrome, with dark and light themes.
- The owner has not downloaded the Windows Desktop bundle yet.
- macOS packaging is deferred; no accepted Mac artifact is available.
- The owner plans to buy a domain for Velyon Core later (entered as «Велорин кор»
  in the conversation). No exact hostname has been provided or purchased here.
  Keep VELIA / Velyon Core as the public product branding. Attach the owned
  domain and update VELIA_WEB_ORIGIN when the actual hostname is provided.
