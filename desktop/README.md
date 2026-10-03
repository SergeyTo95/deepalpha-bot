# VELIA Desktop — developer preview

Desktop client for Velyon Core, with a local DeepSeek Harness runtime pinned to `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. The Electron shell selects a workspace, launches the official `dsh` Web profile on loopback, and opens its authenticated launch URL. Harness owns plugins, filesystem tools, terminal tools, and their approval UI. The upstream MIT license and third-party notices remain in the downloaded checkout.

## Development

Requires Node.js 24 with development headers, Git, pnpm 11, and the platform prerequisites described in the pinned upstream repository. Windows and macOS builds must be tested on their respective platforms.

```sh
cd desktop
npm install
npm run build:harness
npm run connect
npm start
```

`connect` uses the existing `/mobile-connect` pairing flow and stores a dedicated desktop device session. Close the app before pairing again. The client refreshes the short-lived access token; provider keys stay on Velyon Core. Development credentials live under `~/.velia-desktop` with private POSIX file permissions; OS credential-vault storage and Windows ACL hardening are required before public distribution. Agent tools run under the same OS account, so local file permissions do not isolate these user session credentials from the agent.

Optional `VELIA_GATEWAY_URL` selects an HTTPS gateway ending in `/desktop-api/v1`. Its origin must match the paired account. `VELIA_NODE_PATH` selects the Node executable for the local runtime. App data is separate from any existing DeepSeek installation. Product telemetry plugins are disabled; the client uses workspace-write and the upstream ask-for-approval policy.

## Backend preview

Routes are mounted by `run_web_process.py` and disabled by default. To enable a controlled preview after deployment, set:

```text
VELIA_DESKTOP_API_ENABLED=true
VELIA_DESKTOP_PREVIEW_USER_IDS=<explicit Telegram user ids, comma separated>
VELIA_DESKTOP_PRO_MODEL=<tool-capable model served by KIMI_BASE_URL>
```

The gateway uses existing `va_` mobile access tokens and server-side `KIMI_API_KEY` / `KIMI_BASE_URL`. It forwards Chat Completions messages, tool definitions, tool results, and SSE without executing local tools on Railway. It allows one request per user, two concurrent requests per process, 30 calls per user per hour, 1 MiB input, and at most 4096 output tokens. These preview limits are process-local; production billing and distributed quota reservations are not implemented. Raw upstream errors are hidden.

Only PRO and text input are exposed in this preview. Flash requires separately verified tool support, and image input requires a verified vision model. Studio, voice, history sync, and research modules are subsequent integrations. No desktop installer, code signing, automatic updater, GUI smoke, or real-model tool-call acceptance is claimed by this source change. A domain is unnecessary for local development; the existing HTTPS backend can serve the preview gateway.

## Verification

```sh
npm test
python -m pytest tests/test_velia_desktop_gateway.py -q  # from repository root
```

The upstream `npm run build:harness` remains the build gate. Do not distribute an application until that build, authenticated streaming/tool round trips, cancellation, shutdown, and native platform checks pass.
