
import os
import json
from aiohttp import web
from db.database import (
    get_user, get_setting, save_pending, is_subscribed,
    get_subscription_until, get_token_packages,
)

PORT = int(os.getenv("PORT", 3000))

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


async def handle_index(request):
    try:
        with open("webapp/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="Not found", status=404)


async def handle_manifest(request):
    try:
        with open("webapp/tonconnect-manifest.json", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(
            text=content,
            content_type="application/json",
            headers=CORS_HEADERS,
        )
    except FileNotFoundError:
        return web.Response(text="Not found", status=404)


async def handle_static(request):
    filename = request.match_info.get("filename", "")
    filepath = f"webapp/{filename}"
    try:
        if filename.endswith(".png") or filename.endswith(".jpg"):
            with open(filepath, "rb") as f:
                content = f.read()
            content_type = "image/png" if filename.endswith(".png") else "image/jpeg"
            return web.Response(body=content, content_type=content_type)
        else:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            if filename.endswith(".json"):
                content_type = "application/json"
            elif filename.endswith(".js"):
                content_type = "application/javascript"
            elif filename.endswith(".css"):
                content_type = "text/css"
            else:
                content_type = "text/plain"
            return web.Response(
                text=content,
                content_type=content_type,
                headers=CORS_HEADERS,
            )
    except FileNotFoundError:
        return web.Response(text="Not found", status=404)


async def handle_user_api(request):
    user_id = request.match_info.get("user_id", "")
    try:
        uid = int(user_id)
        user = get_user(uid)
        if not user:
            return web.Response(
                text=json.dumps({"error": "Not found"}),
                content_type="application/json",
                headers=CORS_HEADERS,
                status=404,
            )

        subscribed = is_subscribed(uid)
        sub_until = get_subscription_until(uid)

        # Загружаем активные пакеты
        packages_raw = get_token_packages(active_only=True)
        packages = [
            {
                "id": p["id"],
                "name": p["name"],
                "tokens": p["tokens"],
                "price_ton": p["price_ton"],
                "discount_percent": p["discount_percent"],
            }
            for p in packages_raw
        ]

        data = {
            "user_id": user["user_id"],
            "token_balance": user["token_balance"],
            "total_analyses": user["total_analyses"],
            "total_opportunities": user["total_opportunities"],
            "is_vip": user["is_vip"],
            "is_subscribed": subscribed,
            "subscription_until": sub_until,
            "token_price": get_setting("token_price_ton", "0.1"),
            "analysis_price": get_setting("analysis_price_tokens", "10"),
            "opp_price": get_setting("opportunity_price_tokens", "20"),
            "cached_price": get_setting("cached_signal_price_tokens", "5"),
            "subscription_price": get_setting("subscription_price_ton", "1"),
            "subscription_days": get_setting("subscription_days", "30"),
            "packages": packages,
        }

        return web.Response(
            text=json.dumps(data, ensure_ascii=False),
            content_type="application/json",
            headers=CORS_HEADERS,
        )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers=CORS_HEADERS,
            status=500,
        )


async def handle_pending(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        amount = float(data.get("amount", 0))
        payment_type = data.get("payment_type", "tokens")

        if user_id <= 0:
            return web.Response(
                text=json.dumps({"error": "Invalid user_id"}),
                content_type="application/json",
                headers=CORS_HEADERS,
                status=400,
            )

        save_pending(user_id, amount, payment_type)
        print(f"PENDING SAVED: user_id={user_id}, amount={amount}, type={payment_type}")

        return web.Response(
            text=json.dumps({"ok": True}),
            content_type="application/json",
            headers=CORS_HEADERS,
        )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers=CORS_HEADERS,
            status=500,
        )


async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)


async def handle_health(request):
    return web.Response(text="OK")


app = web.Application()
app.router.add_get("/", handle_index)
app.router.add_get("/tonconnect-manifest.json", handle_manifest)
app.router.add_get("/webapp/{filename}", handle_static)
app.router.add_get("/api/user/{user_id}", handle_user_api)
app.router.add_post("/api/pending", handle_pending)
app.router.add_route("OPTIONS", "/api/pending", handle_options)
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
