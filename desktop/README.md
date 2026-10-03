# VELIA Desktop — developer preview 0.2.0

VELIA Electron client for Velyon Core, with the local DeepSeek Harness Web runtime pinned to `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. Harness executes plugins, file operations and terminal commands on the user's computer. The upstream MIT license and third-party notices are retained.

## Start from source

Requires Node.js 24, Git and the platform's native build prerequisites. The desktop package pins pnpm; a separate global pnpm installation is unnecessary.

```sh
cd desktop
npm ci
npm run build:harness
npm start
```

The app opens a Russian connection window. Get an existing VELIA device code in the browser, paste it into the app, then select a working folder. Pairing creates a separate desktop device session and uses the existing VELIA account. `npm run connect` opens the same app; the old plaintext CLI connection flow has been removed.

Optional `VELIA_GATEWAY_URL` selects an HTTPS gateway ending in `/desktop-api/v1`; its origin must match the paired account. `VELIA_NODE_PATH` selects the development Node executable. Packaged apps use their bundled runtime and need neither Node.js nor a Harness checkout installed by the user.

## Session storage and local tools

Persistent device sessions use Electron safeStorage: the OS provides encryption, and the app refuses unavailable encryption or Linux's plaintext backend. A legacy `session.json` is removed only after encrypted migration succeeds. Windows app-data permissions are restricted with icacls; POSIX app data is owner-only. Concurrent model requests share one refresh operation, preserving backend refresh-token rotation.

Account access and refresh tokens remain in the main process and encrypted session store. Harness gets a random key to an authenticated loopback proxy; its transient credentials file contains only that local key. The proxy forwards only model-list and Chat Completions requests to the paired HTTPS gateway, rejects redirects and cancels the upstream fetch when the local stream closes. The local key is valid only while that app process is running; it still authorizes model use through the current app. Plugins run under the OS user account, so this design does not isolate them from the OS account or from other user files.

App data is separate from DeepSeek installations. Product telemetry is disabled. The persona identifies herself as Велия, speaks in feminine grammatical forms, and defaults to Russian. Harness retains its workspace-write policy and approval interface.

## Prepare installers

```sh
npm run build:harness
npm run prepare:runtime
npm run qualify:runtime
npm run qualify:web
npm run qualify:ui
npx electron-builder --publish never --win  # Windows x64 host
npx electron-builder --publish never --mac  # matching native Mac host
```

Build on the target OS and architecture so native addons match the bundled Node executable. Runtime preparation adds the workspace service peers missing from the upstream CLI carrier's production dependency set, deploys a hoisted production tree with copied local vendor overrides, restores the source manifest/workspace settings, copies Node and notices, and refuses a runtime that cannot start. Unused patches for excluded upstream development dependencies are permitted during this production-only deploy; used runtime patches remain applied. `qualify:runtime` uses a deterministic test provider and an actual local read tool; it makes no live model request. `qualify:web` verifies the authenticated loopback page and its cookie handoff. The packaging hook copies the full runtime, rejects dependencies that point outside the package, and repeats tool/Web qualification against the packaged resources. `qualify:ui` checks the sandboxed Electron connection page and IPC on the running platform; native OS encryption is also checked on Windows/macOS.

The paths-scoped workflow provides unsigned NSIS Windows installers and DMG/ZIP Mac previews for both Apple silicon and Intel. It runs the adapter tests, upstream build, native UI/storage checks and installed-runtime tool round trip before uploading artifacts. Pushes to the preview branch or manual dispatch trigger packaging; PR checks run the lightweight adapter suite. Signing, notarization, auto-updates and public release are subsequent work. See `VERIFICATION.md` for the actual results and external blockers; a configured job is not a successful installer build.

## Railway Windows builder

`Dockerfile.railway-windows` builds an unsigned Windows x64 NSIS preview independently of GitHub Actions. The Wine image is pinned by digest; Linux and Windows Node archives use official SHA-256 checksums. Harness is built on Linux, and production deployment includes Windows optional dependencies and the package's Windows prebuilt PTY/FFI binaries. Windows Node under Wine must load those native modules, run the actual read-tool round trip, and open the authenticated Web page. The packaging hook repeats these checks from the final resources and rejects a runtime whose platform differs from the installer.

Deploy a separate builder branch with `desktop/railway-windows.toml` copied to root `railway.toml`. The final image contains only the installer, build manifest and a small read-only download server; it does not start the VELIA backend or retain build credentials. `/health`, `/manifest.json`, and the installer support verification and resumable downloads. Source code belongs in the existing preview PR; the builder branch's root Railway config is kept separate from backend deployment settings.

Wine qualification does not establish native Windows GUI, OS credential-store or terminal acceptance. Real VELIA model access still requires the opt-in gateway deployment. macOS installers use the native macOS jobs. See `VERIFICATION.md` for the actual current builder result.

## Backend preview

Routes are mounted by `run_web_process.py` and disabled by default. A controlled deployment requires:

```text
VELIA_DESKTOP_API_ENABLED=true
VELIA_DESKTOP_PREVIEW_USER_IDS=<explicit Telegram user ids, comma separated>
VELIA_DESKTOP_PRO_MODEL=<verified tool-capable model served by KIMI_BASE_URL>
```

The gateway authenticates existing `va_` device tokens and keeps provider keys server-side. It forwards text Chat Completions, tool definitions, tool results and SSE; it executes no local tools on Railway. Preview limits are one active request per user, two per process, 30 calls per user per hour, 1 MiB input and 4096 output tokens. Limits are process-local, and production billing/distributed reservations are not implemented. Provider redirects and raw upstream errors are rejected or hidden.

Only PRO/text is exposed. Flash tools, vision, Studio, voice and history sync need separate integration and validation. The existing HTTPS backend is enough for controlled preview; a new domain is unnecessary. No production deployment or real-model acceptance is implied by this developer preview.

## Checks

```sh
npm test
python -m pytest tests/test_velia_desktop_gateway.py -q  # from repository root
```
