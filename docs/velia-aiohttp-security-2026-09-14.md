# aiohttp dependency remediation — 2026-09-14

The resolved production requirements in PR #535 still installed aiohttp 3.8.6 because upstream aiogram 2.25.1 (and 2.25.2) requires aiohttp below 3.9. The audit emitted 80 records for that package, including duplicate IDs/aliases. They must not be interpreted as 80 distinct exploitable bugs.

This change installs aiohttp 3.14.3 and a local compatibility package containing byte-for-byte upstream aiogram 2.25.1 runtime source. Only its packaging dependency constraint changes. Provenance, all 136 upstream Git blob hashes, local distribution identity and MIT license are recorded under vendor/aiogram_compat. The application retains its existing bot API.

The Docker build and CI verify source provenance and run pip check. The dependency audit inventories the actual installed runtime in an isolated environment and fails on known vulnerabilities or skipped packages. The verified unchanged aiogram runtime is queried as its upstream version, not omitted due to its local version suffix.

Compatibility tests exercise a real local aiohttp server with bot identity/update retrieval, dispatcher delivery, multipart document upload, streamed download, Telegram-style rate limit errors, session shutdown and native disconnect cancellation. Existing mobile, coding, Kimi, treasury and wallet regressions remain required. Production Docker construction is checked too.

This is a temporary maintenance bridge, not a supported upstream aiogram 3 migration. Its removal requires migrating existing handlers and storage to aiogram 3. The security claim is limited to known Python dependency advisories in the audited installation; OS images, live access controls and GPU acceptance are separate checks.

References: [upstream 2.25.1 source](https://github.com/aiogram/aiogram/tree/88baf0b5828fe35805a58bc48b63615a906f6ea6), [upstream dependency constraint](https://github.com/aiogram/aiogram/blob/88baf0b5828fe35805a58bc48b63615a906f6ea6/requirements.txt), [aiohttp changelog](https://docs.aiohttp.org/en/stable/changes.html).
