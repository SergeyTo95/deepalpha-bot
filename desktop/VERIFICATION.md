# VELIA Desktop preview 0.2.0 verification

Harness revision: `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. Backend base: `aabf3a8ae0d24203761d653918ba35a1a4fa7069`. Validation date: 2026-10-03.

## Passed locally

- 49 backend gateway/relay/Flash tests, including authoritative remote identity verification, owner-only pairing and refresh, revocation of a non-allowlisted session, body limits, cookie isolation, sanitized authority outages, HTTP streaming, Flash tool-message normalization, exact context checks and rejection of identity/provider redirects before they receive credentials.
- 19 desktop behavior tests: isolated absolute preview data directories, HTTPS/loopback configuration, encrypted-session migration and refusal of plaintext storage, pairing-code normalization, token lifetime validation, concurrent refresh coalescing, local proxy authentication, streamed tool bodies and cancellation, Flash's SDK output-budget regression, plus output/exit-status transport for Wine qualifications.
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

- Real-owner VELIA pairing and a Desktop tool round trip through that owner's live account. The separate operator-only live Flash qualification below uses a synthetic identity; it establishes a live model/local-file loop but not owner pairing.
- Successful installer installation and application startup on Windows/macOS, including subprocess cleanup and native credential storage.
- Signing/notarization, automatic updates, distributed quotas and commercial accounting.
- Vision, voice, Studio and mobile history synchronization.

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

## Flash / PRO update accepted

Client source: `799bebcb191c6e08a790d7d352d186cff41998bb`. Gateway deployment `b6f4cab5-a554-4d28-9719-9438216b78be` succeeded at `9f356fecf4314b723ef2e207601be8fe70c96ff3` in the same isolated PR environment. The existing preview Bonsai worker succeeded at deployment `332feb06-7667-468c-8044-cc4fb63e7e37`, source `7da2066f9a9f89380cab6e872ce9d26e74b50a6f`. It uses the previously verified AVX2 runtime and exact weights with an 8192-token context. The production worker, Android configuration and financial settings were not changed.

The gateway build passed 49 Python checks. The local Desktop suite passed 19 Node checks. Real Chromium interaction with the shipped Web view verified exactly VELIA PRO and VELIA FLASH, selection retained after reload, switching back to PRO and zero model calls. Direct DeepSeek API/account adapters and auxiliary model-backed session titles are disabled in this VELIA profile.

The live Flash pre-deploy gate passed a small real `read_probe` call, then launched the exact JavaScript runtime extracted from the qualified Windows ZIP against a private loopback gateway. The installed agent declared all 24 tools, read the newly created local `probe.txt`, sent the real tool result back, and returned `VELIA_FLASH_REAL_READ_OK`. Receipt `VELIA_FLASH_HARNESS_PROBE` reports two live Harness rounds, persona success, 23,372 request characters and `ownerPairingVerified=false`. The separate PRO gate again passed its two correlated tool/SSE provider calls. No owner account was impersonated, and no provider keys were given to Harness.

The pinned client's extra 4096-token safety margin had clamped the Flash tool request's answer limit to one token. The Desktop proxy now restores its declared 512-token budget for that case. The gateway's real template/tokenizer check reserves that restored answer before inference and rejects context overflow. The live gate confirms a complete answer rather than accepting an empty or truncated response. The first full-tool CPU prefill takes several minutes; this preview is not a low-latency benchmark.

Windows resource update: `VELIA-Desktop-0.2.0-flash-pro-update.zip`, 61,845 bytes; SHA-256 `7f2dccb6e6c83f9a56c75f30d1e2e7bd2e28c05367e99a68e561ff8d275f828d`. Updated ASAR SHA-256 `8616718bea25a8605836bbd09e5f23a670a5965e55ba1b9028bf543d9cb390a3`. Only `src/config.mjs` and `src/proxy.mjs` differ from the previous PRO resource update; every other archived module/asset is byte-identical, and both changed modules exactly match reviewed source. ZIP CRCs and all extracted member bytes passed. The archive includes the launcher, a previous-PRO ASAR copy, Russian instructions, the real model-picker screenshot and a machine-readable acceptance manifest.

This accepts the isolated live model and bundled Web/agent integration. Native Windows GUI/credential storage/terminal acceptance and first pairing through the real owner's account remain to be verified on the owner's machine. The PR remains a draft; these checks do not authorize production deployment or imply that the separate full-backend/GitHub Actions gates are green.
