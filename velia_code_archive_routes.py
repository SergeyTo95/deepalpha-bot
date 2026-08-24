from urllib.parse import quote

from aiohttp import web

from services.velia_code_archive_service import (
    get_archive_content,
    verify_archive_signature,
)


def setup_velia_code_archive_routes(app) -> None:
    async def archive_content(request):
        archive_id = str(request.match_info.get("archive_id") or "").strip()
        try:
            user_id = int(request.query.get("user_id") or 0)
            expires_at = int(request.query.get("expires") or 0)
        except (TypeError, ValueError):
            raise web.HTTPForbidden(text="invalid_archive_signature")
        signature = str(request.query.get("signature") or "")
        if not archive_id or user_id <= 0 or not verify_archive_signature(
            archive_id,
            user_id,
            expires_at,
            signature,
        ):
            raise web.HTTPForbidden(text="invalid_archive_signature")

        archive = get_archive_content(archive_id, user_id)
        if not archive:
            raise web.HTTPNotFound(text="archive_not_found")
        filename = str(archive.get("filename") or "VELIA-code.zip")
        ascii_name = "".join(
            char for char in filename if char.isascii() and (char.isalnum() or char in "._-")
        ) or "VELIA-code.zip"
        return web.Response(
            body=archive["bytes"],
            content_type="application/zip",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "X-VELIA-Archive-SHA256": str(archive.get("sha256") or ""),
            },
        )

    app.router.add_get(
        "/api/mobile/code-archives/{archive_id}/content",
        archive_content,
        name="velia_code_archive_content",
    )
