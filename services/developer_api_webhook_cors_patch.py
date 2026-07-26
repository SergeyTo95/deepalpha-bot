from aiohttp import web


@web.middleware
async def webhook_cors_method_middleware(request: web.Request, handler):
    response = await handler(request)
    if request.path.startswith("/api/v1/webhooks"):
        allowed = str(response.headers.get("Access-Control-Allow-Methods", "") or "")
        if allowed and "DELETE" not in {item.strip().upper() for item in allowed.split(",")}:
            response.headers["Access-Control-Allow-Methods"] = allowed + ", DELETE"
    return response


def install(app: web.Application) -> None:
    if app.get("developer_api_webhook_cors_installed"):
        return
    # Insert outside the existing security middleware so its final CORS headers can
    # be extended rather than overwritten.
    app.middlewares.insert(0, webhook_cors_method_middleware)
    app["developer_api_webhook_cors_installed"] = True
