# Studio self-hosted 5-second video hotfix

This hotfix keeps production Studio video on the already accepted 5-second T2V path while removing stale import-time aliases to the legacy video provider and legacy quota function.

When `VELIA_MEDIA_PROVIDER=self_hosted`:

- Studio 5-second text-to-video calls the media worker client directly with the Studio generation id as request id.
- Video capacity is resolved dynamically through `services.velia_videos_service._reserve_capacity`, allowing the installed admin-aware self-hosted quota implementation to apply.
- Reference-conditioned Studio video remains fail-closed.
- 10/15-second video remains outside this hotfix.
- Legacy provider code remains available for explicit rollback.
