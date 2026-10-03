# VELIA Web verification — 2026-10-03

## Account history and token gate — current deployment

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
