# VELIA Desktop preview 0.2.0 verification

Harness revision: `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. Backend base: `aabf3a8ae0d24203761d653918ba35a1a4fa7069`. Validation date: 2026-10-03.

## Passed locally

- 19 backend gateway tests, including HTTP streaming, a correlated tool-result round trip and rejection of a provider redirect before it receives a provider secret.
- 17 desktop behavior tests: HTTPS/loopback configuration, encrypted-session migration and refusal of plaintext storage, pairing-code normalization, token lifetime validation, concurrent refresh coalescing, local proxy authentication, streamed tool bodies and cancellation, plus output/exit-status transport for Wine qualifications.
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

- Real VELIA pairing and live model streaming/tool behavior against a deployed, explicitly enabled preview gateway.
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

This is a portable archive, not an NSIS installer. The NSIS uninstaller-generation helper could not execute in Railway with either the image's Wine or the pinned Wine 11 toolset, so the Railway lane explicitly emits a ZIP; native Windows jobs retain NSIS. Physical Windows Electron GUI, native credential-store, terminal and live VELIA gateway acceptance remain unverified. macOS builds remain blocked by the Actions account issue above. Production backend deployment and the public model gateway were not changed.
