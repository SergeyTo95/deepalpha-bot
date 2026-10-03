# Isolated VELIA Desktop gateway

The Desktop service uses `Dockerfile.gateway` independently of the full VELIA backend. It runs no Telegram bot or payment workers. Existing VELIA device pairing and refresh are relayed only to the configured HTTPS identity authority. Desktop and signed-in Web requests verify the device token through that authority's `/mobile-api/v1/me`; there is no authentication cache or test-token bypass. Optional Web guests use a separate Flash-only route and persistent trial counters. Web Internet search also uses the existing preview PostgreSQL for opaque account-search references and public source facts; see [WEB.md](WEB.md).

Pairing/refresh responses are checked against the same account allowlist before any tokens are returned. Sessions issued to accounts outside the allowlist are revoked. Identity and model redirects are refused. Browser cookies and upstream response headers are never relayed. Authentication payloads are limited to 16 KiB; unexpected fields are rejected. Authentication POSTs are limited to 60 per minute per process.

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

Use references within the existing PR environment; do not copy provider keys to the client. Authenticated accounts in the single-replica preview remain allowlisted; optional Web guests have only the 30-request Flash trial. Shared model admission remains one concurrent request per identity, two per process and 30 per hour. PRO permits 4096 output tokens and requires positive real Credits; Flash permits 512 output tokens. The input cap is 1 MiB. Kimi K3 requests use an explicit low reasoning budget. Commercial debit tariffs and distributed active-generation locking are still outside this preview.

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
