# Browser/mobile feature audit — 2026-10-07

Compared the Android `develop` API and screens with the deployed gateway branch
`ci/velia-desktop-gateway-railway`. The Android default `main` is only a README;
the comparison therefore uses the actual app branch.

| Area | Browser implementation |
| --- | --- |
| Model picker | Flash and existing balance-gated PRO; disabled Quantum with «Скоро · в разработке». Quantum is not an inference ID. |
| Chat | Existing account history and streaming; attachments, rename, pin, public snapshot link, copy and read aloud. |
| Studio | Image provider capabilities, Image 2 when enabled, references, transparency, video durations from status, image-to-video, music/lyrics, sessions, results and downloads. |
| Projects | Passport creation/edit with expected revision, project resource creation and listing. |
| Research | Missions, literature, synthesis, bounded autonomy, reports, sources, claims, evidence and alerts. |
| Medical | Contrast/abdomen confirmations, cases, DICOM ZIP/NIfTI, resumable 8 MiB chunks with SHA-256, completion, result refresh and research creation. |
| Agents | Capability selection, agent creation/list/delete, child conversation creation. |
| Autopilot | Repository selection, bounded missions, activate/pause, enqueue/cancel tasks, run history and CI/review/merge-policy reads. |
| Account | Shared personalization, plugin switches, credits and usage. |
| Voice | User-started browser recognition, language/voice settings, voice dialog and answer playback; answers remain in chat. |

Native offline voice packs, Android Bluetooth/device control, Android background
services and Google Play billing do not transfer to browser APIs. Browser voice
support depends on the browser/device. Physical microphone, acoustic quality and
actual GPU image/video/music/CT inference were not exercised by synthetic QA.

## Security and validation

- Every feature request resolves the existing encrypted, HttpOnly Web session
  and rechecks the account against the identity authority.
- Mutations require same-origin headers; the relay accepts only enumerated
  mobile paths/methods. It cannot relay auth/admin or arbitrary URLs.
- Signed media paths are constrained, with matching account IDs; redirects,
  authority cookies and provider credentials are not forwarded to clients.
- Uploads are bounded, JSON endpoints retain their existing limits, and
  idempotency keys are carried to the authority.
- Gateway build suite: 362 Python tests passed. Core browser tests: 10 passed.
- `desktop/scripts/smoke-web-features.mjs`: real Chromium, synthetic feature
  responses; ten sections, disabled Quantum, Image 2 payload, 15-second video
  payload, canonical autopilot mission IDs, desktop/mobile layouts and no JS
  exceptions. This proves adapter/UI behavior, not live media inference.
- The older `smoke-web-chat.mjs` fixture does not implement the current
  request-intent JSON response and times out expecting sources on its guest
  greeting. That fixture failure is not counted as passing acceptance.

The changes target only the separate browser gateway deployment branch.

## Workspace design verification

Shared cards, form fields, inline checkbox rows, primary actions, grouped navigation, active section indicators and Studio mode buttons support both themes. The sidebar uses one scroll surface to keep all navigation items reachable without clipping on short phone screens. The model list remains openable with Agent enabled and indicates its Flash requirement. Chromium acceptance covers all ten sections, 360/390/768/1440 px widths, no horizontal overflow, checkbox label clicks and selected navigation state. Feature responses in this UI check are synthetic.

Short-screen regression checks cover 393 px width with 640/710/844 px height, model picker bounds in Agent mode and navigation/history separation. The Agent capability flag is synthetic in this UI check; no agent request is sent.

## Autopilot task center

Autopilot now separates scheduled task templates from GitHub development missions. Authenticated schedule routes are explicitly allowlisted in the gateway. The UI checks scheduler and Agent Core availability before offering creation, starts schedules paused, supports daily/weekly/hourly intervals with an IANA timezone, displays the latest job result and explicit approval/run controls. Only server-advertised built-in task tools and connected calendar reads are offered. Chat text may prefill a task draft; it is not a replay of an arbitrary browser instruction. No server worker flags are enabled by this change, and background browser automation or notifications are not implemented.

Validation: 45 focused Python tests, 10 Node tests, and synthetic Chromium checks for weekly payloads, enable/pause, disabled scheduler, approval-before-run, GitHub mission compatibility and mobile layouts. This does not qualify actual production scheduled execution.

## File and answer utilities

Added a local image viewer with zoom, bounded plain-text preview/copy, native PDF open/download, and a clear DOCX preview limitation. Studio image results use the same viewer. Files may be selected, dropped or pasted; selection enforces four files and 15 MiB each. MD/CSV/JSON normalize to the backend-supported text/plain MIME without changing bytes; XLSX/SVG are rejected. Quick attachment prompts fill the composer without sending automatically. Answers and conversations can be downloaded as Markdown.

Original attachment previews are session-memory only (12 files / 60 MiB bounded cache), cleared on account changes/logout; object URLs are revoked when viewers close. Restored history receives allowlisted attachment metadata, never original bytes, extracted text, private URLs or credentials. Originals cannot be reopened after reload unless reselected. DOCX analysis and image understanding continue through the existing backend; this change does not qualify real model inference, PDF OCR or GPU media generation.

Verification: 36 focused server tests, 13 Node tests and synthetic Chromium checks for escaped text previews, image zoom, PDF links, file limits, unsupported formats, Studio viewer and answer/chat downloads.
