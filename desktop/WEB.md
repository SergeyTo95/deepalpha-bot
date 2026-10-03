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

The root serves the responsive monochrome chat. `/mobile-connect` continues to
open the existing VELIA account page. The owner obtains a one-time pairing code
there and enters it in the Web sign-in dialog. Access remains limited to the
explicit owner allowlist. Publicly accessible HTML does not grant model access.

Both VELIA FLASH and VELIA PRO stream real answers through the same admission
counters as Desktop: one concurrent request per user, two per process and 30
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

The browser stores up to 100 conversations locally, names them from the first
message, separates history by the signed-in account, supports search/deletion,
and retains theme and selected mode. Partial interrupted answers are kept and
marked. History is specific to that browser and origin; cross-device sync and
history migration to a future custom domain are separate work.

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
