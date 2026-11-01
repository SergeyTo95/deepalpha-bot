# VELIA Agent Core — project plan

Status: active design / implementation.

Naming: the product/runtime is **VELIA Agent Core**. DeepSeek Harness is only the pinned upstream source lineage retained for licensing and provenance; it is not the product name.

## Goal

One VELIA agent core, shared across three surfaces:

- Desktop — local computer tools: files, terminal, git, processes, browser.
- Browser — cloud/browser tools: navigation, DOM inspection, clicks, forms, downloads, extraction.
- Android — native phone tools: files, camera, share sheet, app/url launch, calendar, voice and later screen/accessibility with explicit user permission.

The model-facing agent loop stays common. Each device/surface advertises only the capabilities it can safely execute.

## Architecture

VELIA AI -> VELIA Agent Core -> capability/tool host -> Desktop / Browser / Android.

A session owns its tool capabilities. The model never receives unavailable tools. Provider/model credentials stay server-side. Device credentials are never exposed to model tools.

Cross-device continuation is a later phase: a task started on Android/Web can hand off to an active Desktop agent when terminal/repository access is needed, and results return to the originating device.

## Browser Agent — current priority

The browser agent uses **VELIA Flash**, not Kimi/VELIA PRO.

Requirements:

1. Flash is the only model exposed by the browser-agent profile. No paid/provider fallback.
2. Chromium automation uses the pinned upstream browser-use integration with Playwright MCP.
3. Browser state belongs to one live agent/session and is cleaned up with that session.
4. Browser tools are intentionally minimal so Flash's 8192-token context is not crowded by Desktop-only tools.
5. First acceptance test is controlled: Flash must navigate a local fixture page with the real browser tool, read a marker, and return it through the real agent loop.
6. Only after that gate passes do we expose a user-facing hosted browser-agent UI.
7. Hosted access must reuse VELIA authentication and fail closed; no anonymous browser-control endpoint.
8. Destructive or account-sensitive actions need explicit approval boundaries before production.

## Planned phases

### Phase 1 — Browser proof — COMPLETE
- VELIA Agent Core browser profile is Flash-only.
- Playwright MCP Chromium provider is live on Railway.
- Desktop shell/filesystem/git tools are excluded from the browser path.
- Live acceptance passed: VELIA Flash -> tool call -> Chromium -> browser result -> Flash final answer.
- Paid-model fallback is disabled.

### Phase 2 — Hosted Browser Agent — IN PROGRESS
- Isolated Railway Browser Agent service is live.
- Authenticated VELIA Web route is wired through the gateway to the private Agent Core service.
- Live Web E2E passed: authenticated Web route -> private Agent Core -> Flash -> Chromium -> answer back.
- Web UI has an AGENT mode for signed-in preview accounts.
- Cold-start handling is implemented for the private Browser Agent service.
- Persistent Browser Agent sessions are implemented: one isolated Chromium profile + resumed headless Agent session per authenticated account and AGENT conversation. Live two-turn continuation passed on Railway. Idle cleanup now stops Chromium to release RAM without deleting its profile; the DSH session id is persisted alongside the Chromium profile and Chromium is started with last-session restore. Restart-recovery acceptance passed. Persistent cookies/localStorage remain in the Chromium profile; session cookies are additionally snapshotted after successful Agent turns and restored through loopback Chrome DevTools Protocol when a new Chromium process is created. The live auth-state probe verifies both cookie and localStorage recovery. A storage sentinel now verifies whether the same session storage is reused by a replacement container; next gate is Railway Volume reuse across two deployments, then multi-tab and account-sensitive approval boundaries.
- Keep VELIA branding; upstream Harness naming is provenance only.

### Phase 3 — Android Tool Host
- Kotlin capability bridge for safe native tools.
- Start with open URL/app, file picker/share, camera/gallery.
- Add calendar/location/etc only with Android runtime permissions and per-action policy.
- Keep the same Agent Core protocol.

### Phase 4 — Cross-device continuation
- Capability registry per signed-in device.
- Handoff of a task to another device only when required capabilities are absent locally.
- Preserve task/session provenance and approval state across handoff.

## Non-goals / safety boundaries

- Browser Agent must not silently inherit Desktop filesystem or shell access.
- No Kimi dependency for Browser Agent inference.
- No provider/API secret in browser JavaScript.
- No silent fallback from Flash to a paid model.
- No claim that a platform capability is verified until its real acceptance gate passes.


### Browser tabs and login handoff
- Browser Agent keeps multiple Chromium tabs inside the same account + AGENT conversation session and uses `browser_tabs` to create/switch tabs without discarding the original page.
- Login-sensitive challenges are never guessed or bypassed. The Agent emits a structured `user_action_required` kind for credentials, OTP, passkey, CAPTCHA, or device approval while keeping the current browser session alive for continuation.

### Browser storage safety
- Hosted profile storage preserves cookies, restored tabs, IndexedDB/Local Storage, downloaded artifacts and current Agent context. Only explicit regenerable Chromium caches, derived Agent attachment cache and old abandoned atomic-write temporary files are swept; active environments and symlinks are excluded.
- Startup and five-minute maintenance log aggregate freed/retained bytes and free disk capacity without user identifiers. Chromium disk/media caches are capped at 16/8 MiB. Admission reserves 128 MiB of disk and limits each profile to 256 MiB by default; exceeding either stops growth without silently deleting user data. Environment settings can tune these limits.
- Existing 30-day inactive-profile retention still applies. A finite volume needs monitoring/capacity planning as durable user data grows; cache cleanup is not unlimited storage.
- Validation: 22 storage/session/Agent tests cover protected artifacts, active environments, symlink isolation, temporary-file age, reserve/profile limits and maintenance/creation locking. Live reclaimed space must be verified in deployment logs.
