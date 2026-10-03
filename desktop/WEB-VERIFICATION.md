# VELIA Web verification — 2026-10-03

## Internet search — current deployment

Implementation: c3847fb6d71964235903a0fc1f4dbd6bbcddfd1d.
Deployed commit: 2282b4dc93689b4820f0056a1ac0afe3a5d2ca19.
Railway deployment: 4a050f98-8719-4a35-9a35-a49b54877c89 — SUCCESS.

- The Internet toggle enriches Web Flash, including guests, and authenticated PRO with bounded live search. Source cards are separate sanitized SSE metadata and remain visible after history reload. Changing the toggle makes no external request.
- 111 focused Python cases are included in the successful Docker build; 109 passed locally together, followed by both added legacy Web route cases. All 27 Desktop/Web Node cases passed locally; the eight Web cases run in Docker.
- Real Chromium fixture passed guest Internet generation and counter persistence, authenticated Flash and PRO Internet requests, native account history reload with original question and source cards, existing code-entry flow, stop, logout, themes and 390px mobile navigation. Four synthetic model requests.
- Tests verify no search for ordinary chat, strict flags and provider/tool override rejection, no search after the 30-message quota or without PRO tokens, no model generation for search failure/empty/unsafe results, public-only source URLs, cached account evidence on retry, and clean Web history restoration.
- The existing configured search provider is reused through Railway variable references. Pre-deploy found three sources including the official Python site and qualified real PostgreSQL evidence persistence with private probe records; it removed only those records. That qualification made zero model calls.
- Guest quota concurrency/persistence and the existing PRO tools/SSE plus shipped Flash Harness two-round file-read gate passed again. No Flash paid fallback or extra model copy was added.
- Public acceptance: root/health and guest profile 200; HTML/app/core/CSS match source bytes; Internet capability true; guest cookie Secure/HttpOnly; account session/history 401; guest PRO and cross-origin request 403.
- One bounded live guest Internet request searched for the official Python download site, delivered a completed Flash SSE answer with three actual sources, and reduced the shared operator allowance from 29 to 28. The next profile read still showed 28. Sources: https://www.python.org/downloads, https://www.python.org, https://docs.python.org/3/tutorial/index.html.
- Native account Internet requests retain the normal authenticated sender and PRO balance checks. Their upstream question carries the evidence block; Web restores the original question using HMAC-bound metadata. Other clients may show that public evidence block. No private owner history or balance was inspected.
- Production backend, Android, Telegram bot and model workers were not redeployed. No PR was merged.

## Guest Flash — previous deployment

Implementation: 2ee1f044ad24e152aa1f7e09e9ca3f28c9bb1afe.
Deployed commit: efd304f258adf0e8803452d17904dc9724859a04.
Railway deployment: 299d1e3f-b944-4122-9816-985d3647816b — SUCCESS.

- Flash works without registration through a separate guest route, with a 30-request browser trial and a separate 30-request-per-UTC-day network guard. The primary allowance does not automatically replenish. The composer displays the remaining count and offers sign-in after exhaustion.
- Guest history stays local and separate from authenticated history. Guest cookies cannot authorize account conversation or raw Desktop endpoints. PRO still requires an authenticated allowlisted preview account with positive real Credits.
- Existing preview PostgreSQL stores only hashed quota keys and integer counts. No database service, model copy or token grants were added; the stable existing Web session key was preserved.
- 87 focused Python tests passed in the Railway Docker build, including 11 guest cases: exactly 30 allowed and the 31st rejected, replay/cookie clearing, server restart, concurrent reservations, origin/credential separation, context overflow, database failure and trusted network-header handling. The 26 Desktop/Web Node tests passed locally.
- Real Chromium fixture passed guest generation without opening sign-in, a 30-to-29 counter that survives reload, disabled guest PRO, isolated account history after login and restored guest history after logout, code-entry flow, positive-balance PRO, stop, themes and mobile navigation. Four synthetic model requests; no real balance was modified.
- The live PostgreSQL pre-deploy qualification accepted exactly 30 concurrent reservations, rejected eight excess reservations and verified exhaustion through a fresh store. It removed only its randomly named private probe rows.
- Existing real-provider qualification passed: PRO tools/SSE and shipped Flash Harness two-round file-read loop with all 24 tools, no paid Flash fallback.
- Public acceptance passed on the published URL: root/health and guest profile 200; HTML/app/core/CSS bytes match source; Secure HttpOnly guest cookie; account session/history still 401; guest PRO and cross-origin generation 403. One bounded live guest Flash request returned a complete SSE answer, reduced the allowance from 30 to 29 and retained 29 on the next guest-profile request.
- Production backend, Android, Telegram bot and worker configuration remain unchanged. No PR was merged.

## Account history and token gate — previous deployment

Implementation: 96bf19255083daffc30680f4645d97aa1a253571.
Deployed commit: 8f0d44a3d3f7a3c467ed2d1020ae4b945a14b93e.
Railway deployment: f9d98cae-d366-4310-b4a0-9f006e534481 — SUCCESS.

- 76 focused Python tests passed locally and in the Railway Docker build; 26 Desktop/Web Node tests passed locally.
- Real Chromium fixture checks passed: existing account conversation and messages, new persisted chats, reload, Flash at zero Credits, PRO disabled at zero and enabled only after a synthetic positive balance, search/deletion, stop, logout, themes and mobile navigation. Three fixture model calls; no real credits were granted.
- Telegram pairing now opens directly from the Web sign-in dialog. The dialog stays open, restores input focus on return, and includes an explicit code-entry button. Browser QA used an intercepted Telegram page; it did not authenticate the owner or send bot messages.
- Zero/negative balance and unavailable economy service reject PRO before provider calls through both browser and raw Desktop endpoints. The account conversation stream has the same gate and shares gateway admission counters. Flash needs no positive balance.
- Conversation APIs use only the signed-in account's server-held access token. Tests cover unauthorized/cross-origin rejection, missing foreign conversations, path injection, upstream metadata removal and the same store for old/new/deleted chats.
- Public acceptance: root/health 200; HTML, app, core and CSS bytes match source; session/history/message requests without a cookie return 401; cross-origin login 403; server module request 404. These checks made zero model calls.
- Existing real-provider pre-deploy gate passed again: PRO tools/SSE and shipped Flash Harness two-round file-read loop with 24 declared tools; no Flash paid fallback.
- The owner's previous Telegram login is user-reported. Their private history and balance were not inspected by the operator; actual account content remains for the owner to verify after refresh.
- Token eligibility checking is included. No purchases, token grants, fee schedule or new debit tariff were added.
- Production backend, Android, Telegram bot and worker configuration were not changed.

## Initial Web deployment

Client/gateway implementation: a7df2d60c66453a7e79997009070efa015c1033a.
Isolated deployment branch: ci/velia-desktop-gateway-railway.
Deployed commit: d33fbb6042fdbcdd4a767b41204cdb612ebcf2c6.
Railway deployment: fb72fe11-aa5f-4b93-9792-4395f9f50c21 — SUCCESS.

URL: https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app/

- Focused HTTP/security suite: 67 Python tests passed locally and in the Railway Docker build.
- Desktop/Web unit suites: 26 Node tests passed locally; the seven Web tests also run in the Docker qualification stage.
- Real Chromium with web security enabled: synthetic HTTP login, Secure/HttpOnly cookie, Flash and PRO routing, streaming, Markdown/code, reload history, stop, search/deletion, logout, persisted themes and 390px mobile navigation passed. Three fixture model calls; no live-owner credentials were used.
- Pre-deploy real-provider gate passed: PRO correlated tool result and live SSE; Flash small tool call and actual shipped Harness file-read loop (two rounds, all 24 tools declared). No Flash paid fallback.
- Public checks passed: root/health 200; four served assets exactly match source bytes; unauthenticated chat requests for both modes and session GET return 401; cross-origin login returns 403; pairing redirects to the existing VELIA account page; nonpublic server files return 404.
- Owner pairing is not claimed as verified. The owner completes the existing one-time code flow in their signed-in VELIA account.
- Production backend/Android were not modified. Web source remains on feature/velia-web-chat. No PR was merged.
- This URL belongs to the existing PR-577 preview environment. Keep that environment until the chat is migrated to its intended permanent environment/domain; closing the parent Desktop PR must not silently remove the working Web preview.
- Desktop download and macOS build remain deferred as recorded in WEB.md.
