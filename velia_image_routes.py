from aiohttp import web

from services.velia_images_service import (
    get_image_content,
    verify_image_signature,
)


def setup_velia_image_routes(app) -> None:
    async def image_content(request: web.Request) -> web.Response:
        image_id = str(request.match_info.get("image_id") or "").strip()
        try:
            user_id = int(request.query.get("user_id") or 0)
            expires_at = int(request.query.get("expires") or 0)
        except (TypeError, ValueError):
            raise web.HTTPForbidden(text="Image access is unavailable")
        signature = str(request.query.get("signature") or "")
        if not image_id or user_id <= 0 or not verify_image_signature(
            image_id,
            user_id,
            expires_at,
            signature,
        ):
            raise web.HTTPForbidden(text="Image access is unavailable")

        image = get_image_content(image_id, user_id)
        if not image:
            raise web.HTTPNotFound(text="Image not found")
        return web.Response(
            body=image["bytes"],
            content_type=image["mime_type"],
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
            },
        )

    app.router.add_get(
        "/api/mobile/images/{image_id}/content",
        image_content,
        name="velia_image_content",
    )
