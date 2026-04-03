
import sys
import os
import asyncio
import json
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_bot
from bot.admin import register_admin
from services.ton_service import get_transactions, parse_payment, calculate_tokens
from db.database import (
    is_tx_processed, save_transaction, add_tokens, ensure_user,
    get_user, add_referral_earnings, get_setting,
)

register_admin(telegram_bot.dp)

PORT = int(os.getenv("PORT", 3000))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://deepalpha-bot-production.up.railway.app")


async def handle_index(request):
    try:
        with open("webapp/index.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="Mini App not found", status=404)


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
                text=json.dumps({"error": "User not found"}),
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


async def handle_health(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/tonconnect-manifest.json", handle_manifest)
    app.router.add_get("/webapp/{filename}", handle_static)
    app.router.add_get("/api/user/{user_id}", handle_user_api)
    app.router.add_get("/health", handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✅ Web server started on port {PORT}")


async def check_ton_payments():
    await asyncio.sleep(15)
    while True:
        try:
            transactions = get_transactions(limit=20)
            for tx in transactions:
                payment = parse_payment(tx)
                if not payment:
                    continue

                tx_hash = payment["tx_hash"]
                user_id = payment["user_id"]
                ton_amount = payment["ton_amount"]

                if is_tx_processed(tx_hash):
                    continue

                tokens = calculate_tokens(ton_amount)
                if tokens <= 0:
                    continue

                ensure_user(user_id)
                new_balance = add_tokens(user_id, tokens)

                referral_bonus_ton = 0
                referrer_id = None
                user = get_user(user_id)
                if user and user.get("referred_by"):
                    referrer_id = user["referred_by"]
                    try:
                        ref_percent = float(get_setting("referral_percent", "10"))
                    except Exception:
                        ref_percent = 10
                    referral_bonus_ton = round(ton_amount * ref_percent / 100, 6)

                    referral_tokens = calculate_tokens(referral_bonus_ton)
                    if referral_tokens > 0:
                        add_tokens(referrer_id, referral_tokens)
                    add_referral_earnings(referrer_id, referral_bonus_ton)

                    try:
                        await telegram_bot.bot.send_message(
                            referrer_id,
                            f"🎉 Ваш реферал пополнил баланс!\n\n"
                            f"💎 Его покупка: {ton_amount:.2f} TON\n"
                            f"🎁 Ваш бонус: {referral_bonus_ton:.4f} TON "
                            f"({referral_tokens} токенов)"
                        )
                    except Exception as e:
                        print(f"REFERRAL NOTIFY ERROR: {e}")

                save_transaction(
                    tx_hash, user_id, ton_amount, tokens,
                    referral_bonus_ton=referral_bonus_ton,
                    referrer_id=referrer_id,
                )

                try:
                    await telegram_bot.bot.send_message(
                        user_id,
                        f"✅ Оплата получена!\n\n"
                        f"💎 TON: {ton_amount:.2f}\n"
                        f"🪙 Начислено токенов: {tokens}\n"
                        f"💰 Баланс: {new_balance} токенов"
                    )
                except Exception as e:
                    print(f"TON NOTIFY ERROR: {e}")

        except Exception as e:
            print(f"TON WORKER ERROR: {e}")

        await asyncio.sleep(60)


async def run_polling():
    while True:
        try:
            print("✅ Starting polling...")
            await telegram_bot.dp.start_polling(
                reset_webhook=True,
                timeout=20,
                relax=0.5,
                fast=True,
            )
        except Exception as e:
            err = str(e)
            if "TerminatedByOtherGetUpdates" in err or "Conflict" in err:
                print("⚠️ Conflict detected, waiting 15 seconds...")
                await asyncio.sleep(15)
            else:
                print(f"Polling error: {e}")
                await asyncio.sleep(5)


async def main():
    try:
        await telegram_bot.bot.delete_webhook(drop_pending_updates=True)
        print("✅ Webhook deleted")
    except Exception as e:
        print(f"Webhook delete error: {e}")

    await start_web_server()

    asyncio.create_task(check_ton_payments())

    print("⏳ Waiting 30 seconds before polling...")
    await asyncio.sleep(30)

    await run_polling()


if __name__ == "__main__":
    asyncio.run(main())
