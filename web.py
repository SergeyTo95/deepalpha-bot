
import os
import json
from aiohttp import web
from db.database import get_user, get_setting, save_pending

PORT = int(os.getenv("PORT", 3000))


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
            headers={"Access-Control-Allow-Origin": "*"}
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
                headers={"Access-Control-Allow-Origin": "*"}
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
                headers={"Access-Control-Allow-Origin": "*"},
                status=404
            )
        data = {
            "user_id": user["user_id"],
            "token_balance": user["token_balance"],
            "total_analyses": user["total_analyses"],
            "total_opportunities": user["total_opportunities"],
            "is_vip": user["is_vip"],
            "token_price": get_setting("token_price_ton", "0.1"),
            "analysis_price": get_setting("analysis_price_tokens", "10"),
            "opp_price": get_setting("opportunity_price_tokens", "20"),
        }
        return web.Response(
            text=json.dumps(data),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            status=500
        )


async def handle_pending(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        amount = float(data.get("amount", 0))
        if user_id <= 0:
            return web.Response(
                text=json.dumps({"error": "Invalid user_id"}),
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
                status=400
            )
        save_pending(user_id, amount)
        print(f"PENDING SAVED: user_id={user_id}, amount={amount}")
        return web.Response(
            text=json.dumps({"ok": True}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
            status=500
        )


async def handle_options(request):
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )


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
