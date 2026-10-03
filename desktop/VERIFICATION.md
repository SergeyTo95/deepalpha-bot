# VELIA Desktop preview 0.2.0 verification

Harness revision: `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. Backend base: `aabf3a8ae0d24203761d653918ba35a1a4fa7069`. Validation date: 2026-10-03.

## Passed locally

- 33 backend gateway/relay tests, including authoritative remote identity verification, owner-only pairing and refresh, revocation of a non-allowlisted session, body limits, cookie isolation, sanitized authority outages, HTTP streaming and rejection of identity/provider redirects before they receive credentials.
- 18 desktop behavior tests: isolated absolute preview data directories, HTTPS/loopback configuration, encrypted-session migration and refusal of plaintext storage, pairing-code normalization, token lifetime validation, concurrent refresh coalescing, local proxy authentication, streamed tool bodies and cancellation, plus output/exit-status transport for Wine qualifications.
- Python compilation of the gateway and route registration; JavaScript syntax checks of shell, scripts and renderer.
- Branded client library build (TypeScript and tsdown) and Web build using the pinned frontend's Vite binary.
- Production runtime deployment, with 89 missing workspace peers included in the carrier. Hoisted dependencies and copied vendor overrides remove references to the build checkout. Bundled Node 24.19.0 and dsh version probe passed on Linux x64.
- Actual installed headless agent loop through the account-isolating local proxy: two streamed provider rounds, the real read tool, correlated `call_velia_read` result and final `VELIA_LOCAL_FILE_OK` response. VELIA persona reached the provider. This used a deterministic mock provider and made no live model call.

- Authenticated bundled Web profile: token-to-cookie handoff and HTML HTTP 200 passed; Node fetch explicitly retains the cookie across the launch redirect.
- Electron Builder produced an unpacked Linux x64 application. Its packaging hook checked that runtime links stay inside the packaged resources, started the bundled Node/CLI there, and repeated both the actual read-tool loop and authenticated Web qualification from those resources. Packaging completed with `VELIA_PACKAGE_QUALIFIED`. This proves the packaged runtime on Linux; it does not establish Windows/macOS installer or Electron GUI acceptance.

## Installer and native UI gates

Windows x64, macOS arm64 and macOS x64 jobs now build their own native runtime, check the Electron connection page and OS-encrypted session storage, run the installed read-tool smoke, and package unsigned previews. Successful native installer builds are not yet claimed.

GitHub Actions run `37105257650` failed before any job step. Its annotation explicitly states: "The job was not started because your account is locked due to a billing issue." Native hosted builds cannot proceed until that account block is resolved. No payment or billing changes were attempted.

The Linux Electron GUI attempt in this execution environment could not complete startup within the qualification deadline; IPC/DBus restrictions were observed. It does not establish Windows/macOS GUI acceptance. The native qualification script has a deadline and remains a required build gate.

## Not yet verified or released

- Real-owner VELIA pairing and a Desktop tool round trip through that owner's live account. The separate operator-only provider probe below passed; it does not establish owner pairing or local file execution with the live model.
- Successful installer installation and application startup on Windows/macOS, including subprocess cleanup and native credential storage.
- Signing/notarization, automatic updates, distributed quotas and commercial accounting.
- Flash tool support, vision, voice, Studio and mobile history synchronization.

Production was not changed, and this pull request remains a developer preview.

## Railway Windows lane

Railway deployment `47919d99-39d2-4656-ad3c-0d14c68729b4` succeeded with builder commit `781a1cd4b23d8d9944fac1fb3b42a387cee3324c`. Service `velia-desktop-windows-build` is isolated from the VELIA backend. The final image contains only the portable ZIP, manifest and read-only download server. Its startup SHA-256 verification and `/health` check passed.

Passed in the actual builder:

- 17 Node behavior tests and two HTTP/checksum artifact-server tests. The transport unit tests use a simulated Wine executable; the following integration gates execute actual Windows binaries.
- Official Windows Node 24.19.0 x64 under Wine, native AMD64 PE module loading for `node-pty` ConPTY and Koffi, and a real Windows API call through Koffi.
- Actual streamed agent loop through the account-isolating local proxy: two provider rounds, real local file reading, correlated tool result and VELIA persona. The provider is deterministic; no live model was called.
- Authenticated bundled Web profile, token-to-cookie handoff and HTML HTTP 200.
- Native-module, read-tool and authenticated Web gates repeated from the final unpacked Windows resources.
- Internal deployment links materialized for Windows. Portable archive paths checked for Windows filename compatibility and case-insensitive collisions. ZIP CRCs passed; all 26,515 physical packaged files matched their extracted SHA-256 hashes.
- Native-module, read-tool and authenticated Web gates passed again from the extracted ZIP resources, followed by `VELIA_WINDOWS_PORTABLE_QUALIFIED` and `VELIA_WINDOWS_PORTABLE_BUILT`.

Artifact: `VELIA-Desktop-0.2.0-win-x64.zip`, 421,480,717 bytes. SHA-256: `54b6ed86541bcd7ce9e874d5d997630c51c905f43f751eb8b88c3db2fdc50bf9`. Built at `2026-10-03T13:44:20.407Z`. Download: <https://velia-desktop-windows-build-production.up.railway.app/>; machine-readable evidence is available at `/manifest.json`.

This is a portable archive, not an NSIS installer. The NSIS uninstaller-generation helper could not execute in Railway with either the image's Wine or the pinned Wine 11 toolset, so the Railway lane explicitly emits a ZIP; native Windows jobs retain NSIS. Physical Windows Electron GUI, native credential-store, terminal and real-owner VELIA gateway acceptance remain unverified. macOS builds remain blocked by the Actions account issue above. Production backend deployment was not changed.

## Isolated live Desktop gateway

Gateway source commit: `6ae399541b39ebbc5089b6dc68629ce5ac822321`. Railway deploy branch `ci/velia-desktop-gateway-railway` adds the independent root Dockerfile without changing the feature PR's backend Dockerfile. Deployment `c6fe8a78-ca7b-445c-a487-5e830736fef5` succeeded at commit `0d6274bae08a65fe2dee182d813eefac19005278` on 2026-10-03. New service `velia-desktop-gateway` (`5948f776-4da6-4907-afa6-bbca0d1532f2`) runs only in `deepalpha-bot-pr-577`, with one replica. No existing production service, variables or deployment gates were changed.

The build executed all 33 HTTP/payload tests. The pre-deploy operator probe made exactly two bounded real-provider requests: a `read_probe` tool call, its correlated fixed test result, then a complete SSE answer `VELIA_LIVE_TOOL_OK`. Receipt `VELIA_DESKTOP_LIVE_PROBE` records the exact deployment commit, successful identity health, unauthenticated identity rejection, tool/SSE success and `owner_pairing_verified=false`. This probe does not authenticate as the owner or read local user files.

Public endpoint: <https://velia-desktop-gateway-deepalpha-bot-pr-577.up.railway.app>. Public checks passed: `/health` 200, model catalog without a token 401, model catalog with a malformed token 401, browser pairing redirect 302 to the existing VELIA `/mobile-connect`, and malformed pairing input 400 without contacting the identity authority. Account identity is checked on every model request through the unchanged existing VELIA Mobile API. Provider keys are referenced inside the PR environment and remain server-side. Only the explicitly configured owner account can use this preview.

The full-backend PR preview still fails its independent Flash/Bonsai pre-deploy probe. Its full gate was preserved. The Desktop service has its own focused build tests and Pro/provider probe. GitHub Actions remains blocked by the account billing lock; these Railway results do not imply all PR checks are green or permission to merge/deploy production.

The Windows live-chat resource update reuses the original qualified ZIP and its native runtime. Only `src/config.mjs` changes inside `app.asar`, adding the explicit preview data directory; all other archived modules/assets are byte-identical. Base ASAR SHA-256: `83371622ba0227124282a1520d8bbabac68ac6c9a22dfae4062a752c385218bc`; updated ASAR: `839594ccb6a9d84d16b38a6bc71163a33ca540cd33bc0eadb82d5e3fe14b75ef`. The extracted update matches reviewed source and passes JS syntax checks. The existing Windows executable's ASAR integrity fuse is disabled; no executable/fuse change is made. `VELIA-Live-Chat.cmd` selects the new gateway and a separate `%LOCALAPPDATA%\VELIA\DesktopGatewayPreview` store. First pairing through the owner's browser and native Windows launch still require the owner's machine.
