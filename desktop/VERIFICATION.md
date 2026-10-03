# VELIA Desktop preview 0.2.0 verification

Harness revision: `5badb15009ae1756c3afe0ae0cef1faafc290ccc`. Backend base: `aabf3a8ae0d24203761d653918ba35a1a4fa7069`. Validation date: 2026-10-03.

## Passed locally

- 19 backend gateway tests, including HTTP streaming, a correlated tool-result round trip and rejection of a provider redirect before it receives a provider secret.
- 14 desktop behavior tests: HTTPS/loopback configuration, encrypted-session migration and refusal of plaintext storage, pairing-code normalization, token lifetime validation, concurrent refresh coalescing, local proxy authentication, streamed tool bodies and cancellation.
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

The separate Docker/Wine builder is prepared to build Windows x64 independently of Actions. It requires actual Windows Node and native PTY/FFI modules to load, then qualifies the streamed read-tool loop and authenticated Web profile both before packaging and from final resources. The final artifact service verifies the installer SHA-256 at startup and supports byte-range downloads.

Preparation checks passed: 17 Node behavior tests (including regular-file transport of process output and exit status), two actual HTTP/checksum artifact-server tests, and syntax checks. These transport unit tests use a simulated Wine executable; they do not establish a successful Windows build. Railway deployment, its packaged-resource qualification and native Windows GUI/storage/terminal acceptance remain pending until separately recorded.
