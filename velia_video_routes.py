from typing import Optional, Tuple

from aiohttp import web

from services.velia_videos_service import (
    get_video_content,
    verify_video_signature,
)


def parse_single_byte_range(
    header_value: str,
    total_size: int,
) -> Optional[Tuple[int, int]]:
    value = str(header_value or "").strip()
    if not value:
        return None
    if total_size <= 0 or not value.lower().startswith("bytes="):
        raise ValueError("invalid_range")
    raw = value[6:].strip()
    if not raw or "," in raw or "-" not in raw:
        raise ValueError("invalid_range")
    start_raw, end_raw = raw.split("-", 1)
    try:
        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else total_size - 1
            if start < 0 or end < start or start >= total_size:
                raise ValueError("invalid_range")
            return start, min(end, total_size - 1)

        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise ValueError("invalid_range")
        start = max(0, total_size - suffix_length)
        return start, total_size - 1
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_range") from exc


def setup_velia_video_routes(app) -> None:
    async def video_content(request: web.Request) -> web.Response:
        video_id = str(request.match_info.get("video_id") or "").strip()
        try:
            user_id = int(request.query.get("user_id") or 0)
            expires_at = int(request.query.get("expires") or 0)
        except (TypeError, ValueError):
            raise web.HTTPForbidden(text="Video access is unavailable")
        signature = str(request.query.get("signature") or "")
        if not video_id or user_id <= 0 or not verify_video_signature(
            video_id,
            user_id,
            expires_at,
            signature,
        ):
            raise web.HTTPForbidden(text="Video access is unavailable")

        video = get_video_content(video_id, user_id)
        if not video:
            raise web.HTTPNotFound(text="Video not found")
        raw = bytes(video["bytes"])
        total_size = len(raw)
        base_headers = {
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{video_id}.mp4"',
            "X-Content-Type-Options": "nosniff",
        }

        range_header = str(request.headers.get("Range") or "")
        if range_header:
            try:
                selected = parse_single_byte_range(range_header, total_size)
            except ValueError:
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={"Content-Range": f"bytes */{total_size}"}
                )
            if selected is None:
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={"Content-Range": f"bytes */{total_size}"}
                )
            start, end = selected
            chunk = raw[start : end + 1]
            headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(len(chunk)),
            }
            return web.Response(
                status=206,
                body=b"" if request.method == "HEAD" else chunk,
                content_type=str(video["mime_type"]),
                headers=headers,
            )

        headers = {**base_headers, "Content-Length": str(total_size)}
        return web.Response(
            body=b"" if request.method == "HEAD" else raw,
            content_type=str(video["mime_type"]),
            headers=headers,
        )

    app.router.add_get(
        "/api/mobile/videos/{video_id}/content",
        video_content,
        name="velia_video_content",
    )
