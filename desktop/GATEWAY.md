# Isolated VELIA Desktop gateway

The Desktop service uses `Dockerfile.gateway` independently of the full VELIA backend. It has no database, Telegram bot or payment workers. Existing VELIA device pairing and refresh are relayed only to the configured HTTPS identity authority. Every model request verifies the device token through that authority's `/mobile-api/v1/me`; there is no authentication cache or test-token bypass.

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

Use references within the existing PR environment; do not copy provider keys to the client. The single replica is a controlled owner-only preview. Existing model limits remain one concurrent request per user, two per process, 30 per hour, 4096 output tokens and 1 MiB input. Kimi K3 requests use an explicit low reasoning budget. Commercial accounting and distributed quotas are still outside this preview.

The build runs `tests/test_velia_desktop_gateway.py` and `tests/test_velia_desktop_relay.py`. The independent service's pre-deploy command is `python -m desktop.probe_gateway --live`. This explicit operator probe makes two bounded provider calls: a correlated test-tool exchange and an SSE answer. It also checks that existing device authentication is enabled and rejects requests without a token. It does not create an account session or establish real-owner pairing. It is not an HTTP endpoint.

The existing full backend retains all its original build, Flash and production gates. Use a separate deployment branch with the gateway Dockerfile copied to root `Dockerfile`; keep that root override out of the Desktop feature PR. Apply the gateway healthcheck `/health` and settings only to the new preview service. A failed provider probe must block this gateway deployment.

The Windows app accepts `VELIA_GATEWAY_URL` and, in the gateway update, `VELIA_DESKTOP_HOME`. The small application-resource update and preview launcher reuse the existing executable and bundled runtime. The launcher selects a separate preview credential directory under `%LOCALAPPDATA%`, preserving the original account store. Close the existing app before updating. A credential store paired to another origin must not be reused silently.

Native Windows GUI/credential-store acceptance and pairing through the real owner's signed-in browser require the owner's machine. Do not report those as verified from synthetic HTTP identities or the provider probe.
