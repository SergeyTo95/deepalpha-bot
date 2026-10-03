# VELIA Web verification — 2026-10-03

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
