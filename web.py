import os
import json
import hmac
import hashlib
import secrets
import asyncio
from urllib.parse import urlencode
import aiohttp
from aiohttp import web
from admin_routes import setup_admin_routes
from services.payment_service import get_pricing_payload
from services.web_auth_service import verify_telegram_init_data, get_cookie_secure_flag
from services.webapp_analysis_service import run_webapp_quick_analysis
from services.webapp_bot_delivery import deliver_webapp_analysis_to_telegram
from services.webapp_top_analysis_service import run_webapp_top_analysis
from services.ton_wallet_service import (
    get_user_ton_balance,
    get_or_create_user_ton_wallet,
    send_ton_from_user_wallet,
    get_ton_send_fee_reserve_nano,
    get_ton_runtime_network,
    get_ton_withdraw_fee_settings,
    list_enabled_ton_jettons,
    get_user_jetton_balances,
)
from services.ton_chain_service import validate_ton_address, ton_to_nano
from db.database import (
    get_user, get_setting, is_subscribed, ensure_user,
    get_subscription_until, get_token_packages,
    get_all_authors, get_author_profile, get_author_post,
    is_author, create_donation, add_pending,
    create_web_session, get_user_by_session, delete_web_session,
    link_web_account, get_web_account,
    add_web_analysis_history, get_web_analysis_history, get_web_analysis_history_item,
    create_web_analysis_job, update_web_analysis_job, get_web_analysis_job,
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


async def handle_app(request):
    try:
        with open("webapp/app.html", "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(text=content, content_type="text/html")
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


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        headers=CORS_HEADERS,
        status=status,
    )


def _safe_int(value, default: int, min_value: int, max_value=None) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        parsed = default
    parsed = max(min_value, parsed)
    if max_value is not None:
        parsed = min(max_value, parsed)
    return parsed


async def handle_user_api(request):
    user_id = request.match_info.get("user_id", "")
    try:
        uid = int(user_id)
        user = get_user(uid)
        if not user:
            return _json_response({"error": "Not found"}, status=404)

        subscribed = is_subscribed(uid)
        sub_until = get_subscription_until(uid)

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
            "is_author": bool(user.get("is_author")),
            "author_balance_ton": user.get("author_balance_ton", 0) or 0,
            "author_withdrawn_ton": user.get("author_withdrawn_ton", 0) or 0,
            "ton_wallet": user.get("ton_wallet", "") or "",
            "extra_watchlist_slots": user.get("extra_watchlist_slots", 0) or 0,
            "author_bio": user.get("author_bio", "") or "",
            "authors_enabled": get_setting("authors_enabled", "on"),
            "donations_enabled": get_setting("donations_enabled", "on"),
            "watchlist_enabled": get_setting("watchlist_enabled", "on"),
            "author_status_price_ton": get_setting("author_status_price_ton", "5"),
            "platform_fee_percent": get_setting("platform_fee_percent", "20"),
            "min_donation_ton": get_setting("min_donation_ton", "0.1"),
            "min_withdrawal_ton": get_setting("min_withdrawal_ton", "1"),
            "watchlist_extra_slots_price": get_setting("watchlist_extra_slots_price", "20"),
            "watchlist_extra_slots_count": get_setting("watchlist_extra_slots_count", "5"),
            "watchlist_limit_regular": get_setting("watchlist_limit_regular", "10"),
            "watchlist_limit_vip": get_setting("watchlist_limit_vip", "50"),
        }

        return _json_response(data)
    except Exception as e:
        print(f"handle_user_api error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_pending(request):
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        amount = float(data.get("amount", 0))
        payment_type = data.get("payment_type", "tokens")

        if user_id <= 0:
            return _json_response({"error": "Invalid user_id"}, status=400)

        valid_types = ("tokens", "subscription", "author_status", "watchlist_slots")
        if payment_type not in valid_types and not payment_type.startswith("donation:"):
            return _json_response({"error": "Invalid payment_type"}, status=400)

        add_pending(user_id, amount, payment_type)
        print(f"PENDING SAVED: user_id={user_id}, amount={amount}, type={payment_type}")

        return _json_response({"ok": True})
    except Exception as e:
        print(f"handle_pending error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_authors_list(request):
    try:
        authors = get_all_authors(limit=100)
        result = []
        for a in authors:
            name = a.get("username") or a.get("first_name") or f"User {a['user_id']}"
            result.append({
                "user_id": a["user_id"],
                "username": a.get("username", ""),
                "first_name": a.get("first_name", ""),
                "display_name": name,
                "bio": a.get("author_bio", "") or "",
                "total_subscribers": a.get("total_subscribers", 0) or 0,
                "total_posts": a.get("total_posts", 0) or 0,
                "total_earned_ton": (
                    (a.get("author_balance_ton", 0) or 0)
                    + (a.get("author_withdrawn_ton", 0) or 0)
                ),
            })
        return _json_response({"authors": result})
    except Exception as e:
        print(f"handle_authors_list error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_author_profile(request):
    author_id = request.match_info.get("author_id", "")
    try:
        aid = int(author_id)
        author = get_author_profile(aid)

        if not author or not author.get("is_author"):
            return _json_response({"error": "Author not found"}, status=404)

        name = author.get("username") or author.get("first_name") or f"User {aid}"

        data = {
            "user_id": aid,
            "username": author.get("username", ""),
            "first_name": author.get("first_name", ""),
            "display_name": name,
            "bio": author.get("author_bio", "") or "",
            "total_subscribers": author.get("total_subscribers", 0) or 0,
            "total_posts": author.get("total_posts", 0) or 0,
            "total_analyses": author.get("total_analyses", 0) or 0,
            "author_since": author.get("author_since"),
        }
        return _json_response(data)
    except Exception as e:
        print(f"handle_author_profile error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_post_details(request):
    post_id = request.match_info.get("post_id", "")
    try:
        pid = int(post_id)
        post = get_author_post(pid)

        if not post:
            return _json_response({"error": "Post not found"}, status=404)

        author_id = post.get("author_id")
        author = get_author_profile(author_id) if author_id else None
        author_name = (
            author.get("username") or author.get("first_name") or f"User {author_id}"
        ) if author else str(author_id)

        data = {
            "id": post["id"],
            "author_id": author_id,
            "author_name": author_name,
            "question": post.get("question", ""),
            "category": post.get("category", ""),
            "display_prediction": post.get("display_prediction", ""),
            "market_probability": post.get("market_probability", ""),
            "confidence": post.get("confidence", ""),
            "alpha_label": post.get("alpha_label", ""),
            "author_comment": post.get("author_comment", "") or "",
            "total_donations_ton": post.get("total_donations_ton", 0) or 0,
            "total_donors": post.get("total_donors", 0) or 0,
            "created_at": post.get("created_at", ""),
        }
        return _json_response(data)
    except Exception as e:
        print(f"handle_post_details error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_create_donation(request):
    try:
        data = await request.json()
        donor_id = int(data.get("donor_id", 0))
        author_id = int(data.get("author_id", 0))
        ton_amount = float(data.get("ton_amount", 0))
        post_id_raw = data.get("post_id")
        comment = (data.get("comment", "") or "").strip()[:500]

        if donor_id <= 0:
            return _json_response({"error": "Invalid donor_id"}, status=400)
        if author_id <= 0:
            return _json_response({"error": "Invalid author_id"}, status=400)
        if donor_id == author_id:
            return _json_response({"error": "Cannot donate to yourself"}, status=400)
        if ton_amount <= 0:
            return _json_response({"error": "Invalid amount"}, status=400)

        if get_setting("donations_enabled", "on") != "on":
            return _json_response({"error": "Donations disabled"}, status=400)

        min_donation = float(get_setting("min_donation_ton", "0.1"))
        if ton_amount < min_donation:
            return _json_response({
                "error": f"Minimum donation: {min_donation} TON"
            }, status=400)

        if not is_author(author_id):
            return _json_response({"error": "User is not an author"}, status=400)

        post_id = None
        if post_id_raw:
            try:
                post_id = int(post_id_raw)
                post = get_author_post(post_id)
                if not post or post.get("author_id") != author_id:
                    post_id = None
            except (ValueError, TypeError):
                post_id = None

        donation_id = create_donation(
            donor_id=donor_id,
            author_id=author_id,
            ton_amount=ton_amount,
            post_id=post_id,
            comment=comment,
            status="pending",
        )

        if not donation_id:
            return _json_response({"error": "Failed to create donation"}, status=500)

        add_pending(donor_id, ton_amount, f"donation:{donation_id}")

        print(
            f"DONATION CREATED: id={donation_id}, donor={donor_id}, "
            f"author={author_id}, amount={ton_amount} TON, post={post_id}"
        )

        return _json_response({
            "ok": True,
            "donation_id": donation_id,
            "amount": ton_amount,
            "payment_type": f"donation:{donation_id}",
        })
    except Exception as e:
        print(f"handle_create_donation error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_public_settings(request):
    try:
        data = {
            "authors_enabled": get_setting("authors_enabled", "on"),
            "donations_enabled": get_setting("donations_enabled", "on"),
            "watchlist_enabled": get_setting("watchlist_enabled", "on"),
            "author_status_price_ton": get_setting("author_status_price_ton", "5"),
            "platform_fee_percent": get_setting("platform_fee_percent", "20"),
            "min_donation_ton": get_setting("min_donation_ton", "0.1"),
            "min_withdrawal_ton": get_setting("min_withdrawal_ton", "1"),
            "subscription_price_ton": get_setting("subscription_price_ton", "1"),
            "subscription_days": get_setting("subscription_days", "30"),
            "watchlist_extra_slots_price": get_setting("watchlist_extra_slots_price", "20"),
            "watchlist_extra_slots_count": get_setting("watchlist_extra_slots_count", "5"),
            "token_price_ton": get_setting("token_price_ton", "0.1"),
        }
        return _json_response(data)
    except Exception as e:
        return _json_response({"error": str(e)}, status=500)


async def handle_buy_slots(request):
    """Покупка доп. слотов Watchlist за токены."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))

        if user_id <= 0:
            return _json_response({"error": "Invalid user_id"}, status=400)

        if get_setting("watchlist_enabled", "on") != "on":
            return _json_response({"error": "Watchlist disabled"}, status=400)

        slots_price = int(get_setting("watchlist_extra_slots_price", "20"))
        slots_count = int(get_setting("watchlist_extra_slots_count", "5"))

        user = get_user(user_id)
        if not user:
            return _json_response({"error": "User not found"}, status=404)

        current_balance = user.get("token_balance", 0) or 0

        if current_balance < slots_price:
            return _json_response({
                "error": f"Insufficient tokens: need {slots_price}, have {current_balance}",
                "need_tokens": slots_price - current_balance,
            }, status=400)

        from db.database import add_tokens, add_watchlist_extra_slots

        new_balance = add_tokens(user_id, -slots_price)
        new_slots = add_watchlist_extra_slots(user_id, slots_count)

        print(
            f"SLOTS PURCHASED: user_id={user_id}, "
            f"price={slots_price} tokens, slots=+{slots_count}, "
            f"new_balance={new_balance}, total_extra={new_slots}"
        )

        return _json_response({
            "ok": True,
            "slots_added": slots_count,
            "total_extra_slots": new_slots,
            "tokens_spent": slots_price,
            "new_balance": new_balance,
        })
    except Exception as e:
        print(f"handle_buy_slots error: {e}")
        import traceback
        traceback.print_exc()
        return _json_response({"error": str(e)}, status=500)


async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)


async def handle_health(request):
    return web.Response(text="OK")


async def handle_api_health(request):
    return _json_response({
        "status": "ok",
        "service": "deepalpha",
        "webapp_enabled": get_setting("webapp_enabled", "on"),
        "maintenance_mode": get_setting("maintenance_mode", "off"),
        "telegram_channel": "active",
    })


async def handle_api_pricing(request):
    return _json_response(get_pricing_payload())


def _set_session_cookie(response: web.Response, token: str) -> None:
    response.set_cookie(
        "deepalpha_session",
        token,
        max_age=2592000,
        httponly=True,
        secure=get_cookie_secure_flag(),
        samesite="Lax",
        path="/",
    )


async def handle_auth_telegram(request):
    try:
        data = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "Invalid JSON"}, status=400)
    init_data = (data.get("init_data") or "").strip()
    bot_token = os.getenv("BOT_TOKEN", "")
    valid, reason, tg_user = verify_telegram_init_data(init_data, bot_token, max_age_seconds=86400)
    if not valid or not tg_user:
        return _json_response({"ok": False, "error": "Unauthorized"}, status=401)

    user_id = int(tg_user.get("id", 0))
    if user_id <= 0:
        return _json_response({"ok": False, "error": "Unauthorized"}, status=401)
    username = tg_user.get("username", "") or ""
    first_name = tg_user.get("first_name", "") or ""

    ensure_user(user_id, username, first_name)
    link_web_account(user_id, "telegram", str(user_id), name=first_name)
    session_token = create_web_session(
        user_id=user_id,
        provider="telegram",
        user_agent=request.headers.get("User-Agent", ""),
        ip=request.remote or "",
    )
    response = _json_response({
        "ok": True,
        "user": {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "provider": "telegram",
        }
    })
    _set_session_cookie(response, session_token)
    return response


async def handle_auth_me(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "auth": {"authenticated": False}})
    return _json_response({
        "ok": True,
        "user": {
            "user_id": current.get("user_id"),
            "username": current.get("username", ""),
            "first_name": current.get("first_name", ""),
            "name": current.get("name", "") or "",
            "email": current.get("email", "") or "",
        },
        "auth": {"provider": current.get("provider", ""), "authenticated": True},
    })




async def handle_webapp_summary(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    user = get_user(user_id)
    if not user:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    raw_language = (user.get("language", "ru") or "ru").lower()
    language = "ru" if raw_language.startswith("ru") else "en"

    tokens = user.get("token_balance")
    if tokens is None or tokens == "":
        tokens = user.get("tokens", 0)
    try:
        tokens = float(tokens)
    except Exception:
        tokens = 0
    if tokens != tokens:
        tokens = 0
    if int(tokens) == tokens:
        tokens = int(tokens)

    subscribed = bool(is_subscribed(user_id) or bool(user.get("is_vip")))

    return _json_response({
        "ok": True,
        "language": language,
        "user": {
            "user_id": user_id,
            "username": user.get("username", "") or "",
            "first_name": user.get("first_name", "") or "",
            "name": user.get("first_name", "") or "",
            "email": user.get("email", "") or "",
            "language": language,
        },
        "balance": {
            "tokens": tokens,
        },
        "subscription": {
            "active": subscribed,
            "until": get_subscription_until(user_id),
            "is_vip": bool(user.get("is_vip")),
            "raw_subscription_until": user.get("subscription_until", "") or "",
        },
        "pricing": {
            "analysis_price_tokens": get_setting("analysis_price_tokens", "10"),
            "top_analysis_price_tokens": get_setting(
                "top_analysis_price_tokens",
                get_setting("opportunity_price_tokens", "70")
            ),
            "token_price_ton": get_setting("token_price_ton", "0.1"),
        },
        "routes": {
            "payment": "/pay",
            "app": "/app",
        },
        "ton_wallet": {
            "enabled": str(get_setting("web_ton_enabled", "off")).lower() == "on",
            "network": get_ton_runtime_network(),
            "token_purchase_enabled": str(get_setting("ton_wallet_token_purchase_enabled", "off")).lower() == "on",
        },
    })


def _current_web_user_id(request) -> int:
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return 0
    user_id = int(current.get("user_id", 0) or 0)
    return user_id if user_id > 0 else 0


async def handle_wallet_ton(request):
    user_id = _current_web_user_id(request)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    get_or_create_user_ton_wallet(user_id)
    balance = get_user_ton_balance(user_id, refresh=False)
    if not balance.get("ok"):
        return _json_response(balance, status=400)
    wallet = get_or_create_user_ton_wallet(user_id)
    return _json_response({
        "ok": True,
        "network": get_ton_runtime_network(),
        "wallet_address": balance.get("wallet_address", ""),
        "balance_nano": str(balance.get("balance_nano", "0")),
        "balance_display": str(balance.get("balance_display", "0")),
        "last_balance_checked_at": balance.get("last_balance_checked_at"),
        "seed_reveal_used": bool(wallet.get("seed_reveal_used")),
        "fee_reserve_nano": str(get_ton_send_fee_reserve_nano()),
        "withdraw_fee_settings": get_ton_withdraw_fee_settings(),
        "jettons": get_user_jetton_balances(user_id, refresh=False),
        "enabled_jettons": list_enabled_ton_jettons(get_ton_runtime_network()),
    })


async def handle_wallet_ton_refresh(request):
    user_id = _current_web_user_id(request)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    balance = get_user_ton_balance(user_id, refresh=True)
    if not balance.get("ok"):
        return _json_response(balance, status=400)
    return await handle_wallet_ton(request)


async def handle_wallet_ton_send(request):
    user_id = _current_web_user_id(request)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    destination_address = str(payload.get("destination_address", "") or "").strip()
    amount_ton = str(payload.get("amount_ton", "") or "").strip()
    comment = str(payload.get("comment", "") or "").strip()
    if not validate_ton_address(destination_address):
        return _json_response({"ok": False, "error": "invalid_address"}, status=400)
    try:
        amount_nano = ton_to_nano(amount_ton)
    except Exception:
        return _json_response({"ok": False, "error": "invalid_amount"}, status=400)
    result = send_ton_from_user_wallet(user_id=user_id, destination_address=destination_address, amount_nano=amount_nano, comment=comment)
    return _json_response(result, status=200 if result.get("ok") else 400)


async def run_webapp_top_analysis_job(job_id: str, user_id: int, url: str, lang: str, provider: str):
    try:
        update_web_analysis_job(job_id, user_id, status="running", progress="top_analysis_started")
        result = await run_webapp_top_analysis(user_id=user_id, url=url, lang=lang)
        if not result.get("ok"):
            err = str(result.get("error", "top_analysis_failed") or "top_analysis_failed")
            update_web_analysis_job(job_id, user_id, status="error", progress="", error=err)
            return
        out = result.get("result", {}) or {}
        history_id = result.get("history_id") or out.get("history_id")
        telegram_delivery = {"attempted": False, "sent": False, "error": ""}
        if str(provider or "").lower() == "telegram":
            try:
                telegram_delivery = await deliver_webapp_analysis_to_telegram(
                    user_id=user_id,
                    market_url=url,
                    raw_result=out,
                    lang=lang,
                )
            except Exception:
                telegram_delivery = {"attempted": True, "sent": False, "error": "delivery_failed"}
        payload = dict(out)
        payload["telegram_delivery"] = telegram_delivery
        update_web_analysis_job(
            job_id,
            user_id,
            status="success",
            progress="",
            history_id=int(history_id) if history_id else None,
            result_json=payload,
            error="",
        )
    except Exception:
        update_web_analysis_job(job_id, user_id, status="error", progress="", error="top_analysis_failed")



async def handle_webapp_analyze(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    user = get_user(user_id)
    if not user:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    url = str(payload.get("url", "") or "").strip()
    mode = str(payload.get("mode", "") or "").strip().lower()

    if (not url) or (len(url) > 500) or ("polymarket.com" not in url.lower()):
        return _json_response({"ok": False, "error": "invalid_url"}, status=400)

    if mode not in ("quick", "top"):
        return _json_response({"ok": False, "error": "invalid_mode"}, status=400)

    raw_language = (user.get("language", "ru") or "ru").lower()
    language = "ru" if raw_language.startswith("ru") else "en"

    if mode == "top":
        result = await run_webapp_top_analysis(user_id=user_id, url=url, lang=language)
        if not result.get("ok"):
            payload = {"ok": False, "error": result.get("error", "top_analysis_failed")}
            if "required_tokens" in result:
                payload["required_tokens"] = result.get("required_tokens")
            return _json_response(payload, status=int(result.get("status_code", 500)))
        bot_delivery = {"attempted": False, "sent": False, "error": ""}
        if str(current.get("provider", "")).lower() == "telegram":
            bot_delivery = await deliver_webapp_analysis_to_telegram(
                user_id=user_id,
                market_url=url,
                raw_result=result.get("result", {}),
                lang=language,
            )
        return _json_response({
            "ok": True,
            "status": "success",
            "analysis_type": "top",
            "charged": bool(result.get("charged")),
            "telegram_delivery": bot_delivery,
            "result": result.get("result", {}),
        })

    result = await run_webapp_quick_analysis(user_id=user_id, url=url, lang=language)
    if not result.get("ok"):
        payload = {"ok": False, "error": result.get("error", "analysis_failed")}
        if "required_tokens" in result:
            payload["required_tokens"] = result.get("required_tokens")
        return _json_response(payload, status=int(result.get("status_code", 500)))
    bot_delivery = {"attempted": False, "sent": False, "error": ""}
    if str(current.get("provider", "")).lower() == "telegram":
        bot_delivery = await deliver_webapp_analysis_to_telegram(
            user_id=user_id,
            market_url=url,
            raw_result=result.get("result", {}),
            lang=language,
        )

    return _json_response({
        "ok": True,
        "status": result.get("status", "success"),
        "analysis_type": "quick",
        "charged": bool(result.get("charged")),
        "result": result.get("result", {}),
        "telegram_delivery": bot_delivery,
    })


async def handle_webapp_analyze_start(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0 or not get_user(user_id):
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    url = str(payload.get("url", "") or "").strip()
    mode = str(payload.get("mode", "") or "").strip().lower()
    if (not url) or (len(url) > 500) or ("polymarket.com" not in url.lower()):
        return _json_response({"ok": False, "error": "invalid_url"}, status=400)
    if mode != "top":
        return _json_response({"ok": False, "error": "invalid_mode"}, status=400)
    user = get_user(user_id)
    raw_language = (user.get("language", "ru") or "ru").lower()
    language = "ru" if raw_language.startswith("ru") else "en"
    provider = str(current.get("provider", "") or "")
    job_id = create_web_analysis_job(user_id=user_id, analysis_type="top", market_url=url)
    asyncio.create_task(run_webapp_top_analysis_job(job_id, user_id, url, language, provider))
    return _json_response({"ok": True, "job_id": job_id, "status": "queued", "analysis_type": "top"})


async def handle_webapp_analyze_status(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    job_id = str(request.match_info.get("job_id", "") or "").strip()
    if not job_id:
        return _json_response({"ok": False, "error": "not_found"}, status=404)
    job = get_web_analysis_job(job_id, user_id)
    if not job:
        return _json_response({"ok": False, "error": "not_found"}, status=404)
    data = {
        "ok": True,
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "analysis_type": job.get("analysis_type"),
        "progress": job.get("progress") or "",
    }
    if job.get("status") == "success":
        result = job.get("result") or {}
        data["history_id"] = job.get("history_id")
        data["result"] = result
        if isinstance(result, dict) and result.get("telegram_delivery"):
            data["telegram_delivery"] = result.get("telegram_delivery")
    elif job.get("status") == "error":
        data["error"] = job.get("error") or "top_analysis_failed"
    return _json_response(data)


async def handle_webapp_history(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    limit = _safe_int(request.query.get("limit", "10"), default=10, min_value=1, max_value=30)
    offset = _safe_int(request.query.get("offset", "0"), default=0, min_value=0)
    items = get_web_analysis_history(user_id, limit=limit, offset=offset)
    return _json_response({
        "ok": True,
        "items": items,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(items),
            "has_more": len(items) == limit,
        },
    })


async def handle_webapp_history_item(request):
    token = request.cookies.get("deepalpha_session", "")
    current = get_user_by_session(token) if token else None
    if not current:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    user_id = int(current.get("user_id", 0) or 0)
    if user_id <= 0:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        item_id = int(request.match_info.get("item_id", "0"))
    except Exception:
        return _json_response({"ok": False, "error": "invalid_id"}, status=400)
    item = get_web_analysis_history_item(user_id, item_id)
    if not item:
        return _json_response({"ok": False, "error": "not_found"}, status=404)
    return _json_response({"ok": True, "item": item})


async def handle_auth_logout(request):
    token = request.cookies.get("deepalpha_session", "")
    if token:
        delete_web_session(token)
    response = _json_response({"ok": True})
    response.del_cookie("deepalpha_session", path="/")
    return response


async def handle_google_start(request):
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    redir = os.getenv("GOOGLE_REDIRECT_URI", "")
    if not cid or not redir:
        return _json_response({"ok": False, "error": "google_login_not_configured"})
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": cid,
        "redirect_uri": redir,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    response = web.HTTPFound(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")
    response.set_cookie("deepalpha_google_state", state, max_age=600, httponly=True, secure=get_cookie_secure_flag(), samesite="Lax", path="/")
    raise response


def _oauth_html(message):
    return web.Response(text=(
        "<!doctype html><html><body style='font-family:sans-serif;padding:24px;'>"
        + message + "<br/><br/><a href='/app'>Back to app</a></body></html>"
    ), content_type="text/html")


def _google_user_id(provider_sub):
    # Stable high-range BIGINT namespace to avoid collisions with Telegram numeric IDs.
    digest = hashlib.sha256(("google:" + provider_sub).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") & ((1 << 62) - 1)
    return (1 << 61) + value


async def handle_google_callback(request):
    cid = os.getenv("GOOGLE_CLIENT_ID", "")
    secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redir = os.getenv("GOOGLE_REDIRECT_URI", "")
    if not cid or not secret or not redir:
        return _json_response({"ok": False, "error": "google_login_not_configured"})

    error = (request.query.get("error", "") or "").strip()
    if error:
        return _oauth_html("Google sign-in was canceled or failed.")

    code = (request.query.get("code", "") or "").strip()
    state = (request.query.get("state", "") or "").strip()
    expected = (request.cookies.get("deepalpha_google_state", "") or "").strip()
    if (not code) or (not state) or (not expected) or (not hmac.compare_digest(state, expected)):
        return _json_response({"ok": False, "error": "invalid_oauth_state"}, status=401)

    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://openidconnect.googleapis.com/v1/userinfo"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data={
                "code": code,
                "client_id": cid,
                "client_secret": secret,
                "redirect_uri": redir,
                "grant_type": "authorization_code",
            }) as token_resp:
                if token_resp.status != 200:
                    return _oauth_html("Google sign-in failed during token exchange.")
                token_payload = await token_resp.json()

            access_token = str(token_payload.get("access_token", "") or "")
            if not access_token:
                return _oauth_html("Google sign-in failed: missing access token.")

            async with session.get(userinfo_url, headers={"Authorization": "Bearer " + access_token}) as userinfo_resp:
                if userinfo_resp.status != 200:
                    return _oauth_html("Google sign-in failed when fetching profile.")
                profile = await userinfo_resp.json()
    except Exception:
        return _oauth_html("Google sign-in is temporarily unavailable. Please try again.")

    provider_sub = str(profile.get("sub", "") or "").strip()
    if not provider_sub:
        return _oauth_html("Google sign-in failed: invalid account profile.")
    email = str(profile.get("email", "") or "").strip()
    name = str(profile.get("name", "") or "").strip()
    picture = str(profile.get("picture", "") or "").strip()

    account = get_web_account("google", provider_sub)
    if account and account.get("user_id"):
        user_id = int(account.get("user_id"))
    else:
        user_id = _google_user_id(provider_sub)
        username = email.split("@", 1)[0] if email and "@" in email else ""
        ensure_user(user_id, username, name)

    link_web_account(
        user_id,
        provider="google",
        provider_sub=provider_sub,
        email=email,
        name=name,
        avatar_url=picture,
    )

    session_token = create_web_session(
        user_id=user_id,
        provider="google",
        user_agent=request.headers.get("User-Agent", ""),
        ip=request.remote or "",
    )

    base_url = (os.getenv("WEB_APP_BASE_URL", "") or "").strip()
    redirect_url = (base_url.rstrip("/") + "/app") if base_url else "/app"
    response = web.HTTPFound(redirect_url)
    _set_session_cookie(response, session_token)
    response.del_cookie("deepalpha_google_state", path="/")
    raise response


app = web.Application()

app.router.add_get("/", handle_index)
app.router.add_get("/pay", handle_index)
app.router.add_get("/app", handle_app)
app.router.add_get("/tonconnect-manifest.json", handle_manifest)
app.router.add_get("/webapp/{filename}", handle_static)

app.router.add_get("/api/user/{user_id}", handle_user_api)

app.router.add_post("/api/pending", handle_pending)
app.router.add_route("OPTIONS", "/api/pending", handle_options)

app.router.add_get("/api/authors", handle_authors_list)
app.router.add_get("/api/author/{author_id}", handle_author_profile)
app.router.add_get("/api/post/{post_id}", handle_post_details)
app.router.add_post("/api/donation/create", handle_create_donation)
app.router.add_route("OPTIONS", "/api/donation/create", handle_options)
app.router.add_get("/api/settings/public", handle_public_settings)

app.router.add_post("/api/watchlist/buy_slots", handle_buy_slots)
app.router.add_route("OPTIONS", "/api/watchlist/buy_slots", handle_options)

app.router.add_get("/health", handle_health)
app.router.add_get("/api/health", handle_api_health)
app.router.add_get("/api/pricing", handle_api_pricing)
app.router.add_post("/api/auth/telegram", handle_auth_telegram)
app.router.add_route("OPTIONS", "/api/auth/telegram", handle_options)
app.router.add_get("/api/auth/me", handle_auth_me)
app.router.add_get("/api/webapp/summary", handle_webapp_summary)
app.router.add_post("/api/webapp/analyze", handle_webapp_analyze)
app.router.add_post("/api/webapp/analyze/start", handle_webapp_analyze_start)
app.router.add_get("/api/webapp/analyze/status/{job_id}", handle_webapp_analyze_status)
app.router.add_get("/api/wallets/ton", handle_wallet_ton)
app.router.add_post("/api/wallets/ton/refresh", handle_wallet_ton_refresh)
app.router.add_post("/api/wallets/ton/send", handle_wallet_ton_send)
app.router.add_route("OPTIONS", "/api/webapp/analyze", handle_options)
app.router.add_route("OPTIONS", "/api/webapp/analyze/start", handle_options)
app.router.add_route("OPTIONS", "/api/webapp/analyze/status/{job_id}", handle_options)
app.router.add_get("/api/webapp/history", handle_webapp_history)
app.router.add_get("/api/webapp/history/{item_id}", handle_webapp_history_item)
app.router.add_route("OPTIONS", "/api/wallets/ton", handle_options)
app.router.add_route("OPTIONS", "/api/wallets/ton/refresh", handle_options)
app.router.add_route("OPTIONS", "/api/wallets/ton/send", handle_options)
app.router.add_post("/api/auth/logout", handle_auth_logout)
app.router.add_route("OPTIONS", "/api/auth/logout", handle_options)
app.router.add_get("/api/auth/google/start", handle_google_start)
app.router.add_get("/api/auth/google/callback", handle_google_callback)

setup_admin_routes(app)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
