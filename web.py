import os
import json
from aiohttp import web
from db.database import (
    get_user, get_setting, add_pending, is_subscribed,
    get_subscription_until, get_token_packages,
    get_all_authors, get_author_profile, get_author_post,
    is_author, create_donation,
    get_author_profile as get_author_db,
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


def _json_response(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        content_type="application/json",
        headers=CORS_HEADERS,
        status=status,
    )


# ═══════════════════════════════════════════
# USER API (расширенный)
# ═══════════════════════════════════════════

async def handle_user_api(request):
    user_id = request.match_info.get("user_id", "")
    try:
        uid = int(user_id)
        user = get_user(uid)
        if not user:
            return _json_response({"error": "Not found"}, status=404)

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
            # ═══ NEW: author / donations / watchlist slots ═══
            "is_author": bool(user.get("is_author")),
            "author_balance_ton": user.get("author_balance_ton", 0) or 0,
            "author_withdrawn_ton": user.get("author_withdrawn_ton", 0) or 0,
            "ton_wallet": user.get("ton_wallet", "") or "",
            "extra_watchlist_slots": user.get("extra_watchlist_slots", 0) or 0,
            "author_bio": user.get("author_bio", "") or "",
            # Публичные настройки для UI
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
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# PENDING API (совместимость со старым WebApp)
# ═══════════════════════════════════════════

async def handle_pending(request):
    """Старый эндпоинт — сохраняет pending для tokens/subscription."""
    try:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        amount = float(data.get("amount", 0))
        payment_type = data.get("payment_type", "tokens")

        if user_id <= 0:
            return _json_response({"error": "Invalid user_id"}, status=400)

        # Валидация типа для безопасности
        valid_types = ("tokens", "subscription", "author_status", "watchlist_slots")
        if payment_type not in valid_types and not payment_type.startswith("donation:"):
            return _json_response({"error": "Invalid payment_type"}, status=400)

        add_pending(user_id, amount, payment_type)
        print(f"PENDING SAVED: user_id={user_id}, amount={amount}, type={payment_type}")

        return _json_response({"ok": True})
    except Exception as e:
        print(f"handle_pending error: {e}")
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# AUTHORS LIST
# ═══════════════════════════════════════════

async def handle_authors_list(request):
    """Возвращает список всех авторов для выбора при донате."""
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
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# AUTHOR PROFILE
# ═══════════════════════════════════════════

async def handle_author_profile(request):
    """Возвращает публичный профиль одного автора."""
    author_id = request.match_info.get("author_id", "")
    try:
        aid = int(author_id)
        author = get_author_profile(aid)

        if not author or not author.get("is_author"):
            return _json_response({"error": "Author not found"}, status=404)

        name = (
            author.get("username")
            or author.get("first_name")
            or f"User {aid}"
        )

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
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# POST DETAILS
# ═══════════════════════════════════════════

async def handle_post_details(request):
    """Возвращает детали поста для отображения на странице доната."""
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
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# CREATE DONATION
# ═══════════════════════════════════════════

async def handle_create_donation(request):
    """
    Создаёт запись доната в author_donations со статусом 'pending'.
    Создаёт pending_payment с payment_type="donation:<id>".
    Возвращает donation_id для последующей оплаты.
    """
    try:
        data = await request.json()
        donor_id = int(data.get("donor_id", 0))
        author_id = int(data.get("author_id", 0))
        ton_amount = float(data.get("ton_amount", 0))
        post_id_raw = data.get("post_id")
        comment = (data.get("comment", "") or "").strip()[:500]

        # Валидация
        if donor_id <= 0:
            return _json_response({"error": "Invalid donor_id"}, status=400)
        if author_id <= 0:
            return _json_response({"error": "Invalid author_id"}, status=400)
        if donor_id == author_id:
            return _json_response({"error": "Cannot donate to yourself"}, status=400)
        if ton_amount <= 0:
            return _json_response({"error": "Invalid amount"}, status=400)

        # Проверки по настройкам
        if get_setting("donations_enabled", "on") != "on":
            return _json_response({"error": "Donations disabled"}, status=400)

        min_donation = float(get_setting("min_donation_ton", "0.1"))
        if ton_amount < min_donation:
            return _json_response({
                "error": f"Minimum donation: {min_donation} TON"
            }, status=400)

        # Проверяем что автор существует
        if not is_author(author_id):
            return _json_response({"error": "User is not an author"}, status=400)

        # Парсим post_id (необязательный)
        post_id = None
        if post_id_raw:
            try:
                post_id = int(post_id_raw)
                # Проверяем что пост существует и принадлежит автору
                post = get_author_post(post_id)
                if not post or post.get("author_id") != author_id:
                    post_id = None
            except (ValueError, TypeError):
                post_id = None

        # Создаём запись доната (статус 'pending')
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

        # Создаём pending_payment с типом donation:<id>
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
        return _json_response({"error": str(e)}, status=500)


# ═══════════════════════════════════════════
# PUBLIC SETTINGS (для любого клиента)
# ═══════════════════════════════════════════

async def handle_public_settings(request):
    """Публичные настройки системы — доступны без user_id."""
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


# ═══════════════════════════════════════════
# OPTIONS / HEALTH
# ═══════════════════════════════════════════

async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)


async def handle_health(request):
    return web.Response(text="OK")


# ═══════════════════════════════════════════
# APP
# ═══════════════════════════════════════════

app = web.Application()

# Страницы и статика
app.router.add_get("/", handle_index)
app.router.add_get("/tonconnect-manifest.json", handle_manifest)
app.router.add_get("/webapp/{filename}", handle_static)

# User API (оставлено для обратной совместимости)
app.router.add_get("/api/user/{user_id}", handle_user_api)

# Pending payments — универсальный эндпоинт
app.router.add_post("/api/pending", handle_pending)
app.router.add_route("OPTIONS", "/api/pending", handle_options)

# ═══ NEW: Authors / Donations / Settings ═══
app.router.add_get("/api/authors", handle_authors_list)
app.router.add_get("/api/author/{author_id}", handle_author_profile)
app.router.add_get("/api/post/{post_id}", handle_post_details)
app.router.add_post("/api/donation/create", handle_create_donation)
app.router.add_route("OPTIONS", "/api/donation/create", handle_options)
app.router.add_get("/api/settings/public", handle_public_settings)

# Health
app.router.add_get("/health", handle_health)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)





```bash
git add web.py
git commit -m "Add authors/donations/post API endpoints"
git pull --rebase origin main
git push
```

## Как проверить что всё работает

После деплоя открой в браузере:

```
https://deepalpha-bot-production.up.railway.app/api/authors
```

Должен вернуть JSON со списком авторов (пока может быть пустой список если авторов нет).

```
https://deepalpha-bot-production.up.railway.app/api/settings/public
```

Должен вернуть все настройки.

```
https://deepalpha-bot-production.up.railway.app/api/user/5811340792
```

(твой ADMIN_ID) — должен вернуть твой профиль с полем `"is_author": true` (после того как выдал себе статус).

Если все три URL возвращают валидный JSON — бэкенд готов.

## Что дальше

Последний шаг — обновить **`webapp/index.html`**. Я возьму твой текущий файл, сохраню весь дизайн и вкладки tokens/subscription как есть, и добавлю 3 новые:

1. **📢 Стать автором** — при открытии с `?tab=author_status`. Простая карточка "Купи статус за N TON" и кнопка оплаты.

2. **💝 Донат автору** — при открытии с `?tab=donate` или `?tab=donate&author=123&post=45`. Если автор/пост в URL — автоматически выбираются. Если нет — список авторов для выбора. Поле суммы, поле комментария, кнопка "Отправить донат".

3. **⭐ Доп. слоты Watchlist** — при открытии с `?tab=watchlist_slots`. Карточка "+N слотов за N TON" и кнопка.

Вкладки tokens/subscription показываются только когда `?tab` не указан или равен `tokens`/`subscription`. Остальные вкладки — когда явно нужны.

Готов? Скажи "го index.html" — дам полный финальный фронтенд.
