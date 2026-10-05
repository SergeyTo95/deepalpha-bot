"""Minimal health surface for the Browser Agent acceptance service."""
import os
from aiohttp import web


async def health(_request):
    return web.json_response({
        "ok": True,
        "service": "velia-agent-core-browser",
        "model": "velia-flash",
        "browser": "playwright-mcp",
        "public_agent": False,
        "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA", ""),
    })


app = web.Application()
app.router.add_get("/health", health)


if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=None)
