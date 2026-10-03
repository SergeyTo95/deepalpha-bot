# Verification of the initial desktop preview

Validated against Harness commit `5badb15009ae1756c3afe0ae0cef1faafc290ccc` and backend base `aabf3a8ae0d24203761d653918ba35a1a4fa7069`.

- Backend gateway tests: 17 passed. Covers opt-in gating, authentication, user allowlist, tool-call correlation, Harness text-content blocks, malformed bodies, unsupported vision and bounded output.
- Desktop configuration and session tests: 5 passed. Covers HTTPS endpoints, loopback launch URLs, credential references, disabled telemetry, refresh-token rotation, and failed refresh retention.
- Python compilation of `velia_desktop_routes.py` and `run_web_process.py`: passed.
- JavaScript syntax checks for the shell and preparation/connection scripts: passed.
- Upstream host TypeScript build: passed.
- Upstream `pnpm run build:lib`: passed.
- Upstream `pnpm run build:web`, after the library build completed: passed.
- Upstream native system module: built with official Node.js v24.19.0 Node-API headers in a scratch Node installation.
- Pinned Web profile with the VELIA provider/persona patch: started; authenticated loopback page returned HTTP 200 and set its signed cookie. No model request was issued.

The top-level upstream build command encountered an environment restriction on tsx's Unix IPC socket, and the supplied Node installation lacked development headers. The constituent native/library/Web steps above were run separately. This is not a claim that the unmodified top-level build passed here.

Still required: launch the Electron shell on Windows/macOS, pair a real desktop device, deploy and explicitly enable the preview gateway, verify live streaming/tool calls and cancellation, validate subprocess shutdown on both platforms, move credentials to OS-managed secure storage, add commercial accounting, and package/sign installers. Production was not changed.
