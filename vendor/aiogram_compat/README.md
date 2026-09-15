# Temporary aiogram 2 compatibility package

The application uses the aiogram 2 API. Upstream 2.25.1 and 2.25.2 metadata constrain aiohttp below 3.9, keeping known HTTP vulnerabilities in the public web server.

This package contains the 136 Python runtime files from upstream aiogram 2.25.1 commit 88baf0b5828fe35805a58bc48b63615a906f6ea6, unmodified, with the original MIT license. Only packaging is maintained here: distribution version 2.25.1+velia.1 and aiohttp 3.14.3. Existing bot handlers do not migrate to the incompatible aiogram 3 API.

Run python vendor/aiogram_compat/verify_upstream.py to verify every runtime file against its upstream Git blob hash. CI and the Docker build require this verification. Runtime edits must not be hidden by changing the manifest; review any future patch explicitly and revise the audit provenance mapping.

The audit installs the actual application requirements in an isolated environment, runs pip check, and audits every installed package. Because the aiogram runtime is verified unchanged, its local distribution version is mapped to upstream 2.25.1 for the vulnerability database query. No vulnerability IDs are ignored.

Integration tests use a local HTTP server and exercise getMe, update retrieval/dispatch, multipart document upload, file download, provider errors and shutdown. No Telegram credentials or live messages are used.

Maintain this as a temporary bridge: migrate application handlers to supported aiogram 3, then remove this directory. This package is not an upstream aiogram release and does not claim upstream support for this dependency combination.
