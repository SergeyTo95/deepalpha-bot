# VELIA efficiency and security — 2026-09-14

## Changes

- Cache successful video/music descriptions for ten minutes, separately per user and full instruction (including duration and instrumental mode); coalesce simultaneous duplicates. The bounded cache does not contain generated media or song lyrics. Model/provider configuration participates in the hashed key. Failures still fall back to the original user request.
- Use low reasoning and a 3,072-token initial completion budget for description rewriting, with truncation recovery capped at 8,192. Existing complex coding/review reasoning and lyric generation remain unchanged. Actual savings require billed usage measurement; a lower token ceiling is not a measured percentage saving.
- Preserve explicitly requested on-screen text/logos in video descriptions and prevent the instrumental music prompt from asking for vocals.
- Deduplicate repository evidence, block known credential paths from reads/search/write preparation, and redact detectable embedded credentials before model context construction. Keep source line numbering and original patch state intact. Redaction is a defensive heuristic, not a substitute for secret scanning, credential rotation or repository access controls.
- Reject authenticated HTTP redirects to prevent credential forwarding. Bound worker JSON responses, validate artifact path components, stream artifact downloads/hashes and close responses on all outcomes.
- Update explicit vulnerable Requests, python-dotenv, Pillow and pypdf pins.

## Validation and remaining audit scope

Regression tests cover isolation/concurrent reuse/expiry, Kimi feature budgets, redirect and streaming failures, artifact identifiers, sensitive paths/redaction, evidence deduplication and instrumental duration.

A focused explicit-pin audit of the selected updated dependencies found no known advisories during preparation. The new CI audit resolves the complete backend requirements and publishes every finding. The legacy aiogram 2.25.1 constraint requires aiohttp below 3.9; migrating this stack needs separate compatibility validation across the Telegram bot and web server. This is an unresolved dependency risk, not a clean security bill of health.

No actual RTX 5090 acceptance or new GPU speed/quality measurement was possible in this change. Worker improvements are tracked separately in velia-media-worker PR #39; parent H3 release gates remain in place.

## Operational controls

Set VELIA_MEDIA_PROMPT_CACHE_ENABLED=false to disable rewrite reuse. KIMI_MEDIA_PROMPT_MAX_COMPLETION_TOKENS adjusts the media description budget within its bounds. Existing per-request/cycle/user accounting remains authoritative for real provider calls. The cache is in-process and does not promise deduplication across replicas.

References: [Requests advisory](https://github.com/psf/requests/security/advisories/GHSA-9hjg-9r4m-mvj7), [Pillow release fixes](https://pillow.readthedocs.io/en/stable/releasenotes/12.3.0.html).
