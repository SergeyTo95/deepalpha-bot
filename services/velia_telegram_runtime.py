"""Opt-in bot inside the existing web process, with shared account/chat storage.

Updates are checkpointed before dispatch: a crash never replays a task or code.
An interrupted response remains in shared chat history; retry is user-initiated.
"""
import asyncio
from contextlib import suppress
import hashlib
import json
import logging
import os
import time
from types import SimpleNamespace

import aiohttp
from db.database import get_connection, ensure_user, is_user_banned
from services import velia_telegram_config as config

logger = logging.getLogger(__name__)
MODES = {"/flash": "flash", "/quantum": "quantum", "/pro": "pro"}


class TelegramError(RuntimeError):
    def __init__(self, code=0, retry_after=5):
        super().__init__("telegram_request_failed")
        self.code = code
        self.retry_after = min(60, max(1, int(retry_after)))


def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_telegram_offsets (
            bot_id BIGINT PRIMARY KEY, next_update BIGINT NOT NULL DEFAULT 0)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS velia_telegram_chats (
            bot_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
            conversation_id TEXT, mode TEXT NOT NULL DEFAULT 'flash'
            CHECK (mode IN ('flash','quantum','pro')), PRIMARY KEY(bot_id,user_id))""")
    conn.commit()


def offset(conn, bot_id, claim=None):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO velia_telegram_offsets(bot_id) VALUES (%s) ON CONFLICT DO NOTHING", (bot_id,))
        cur.execute("SELECT next_update FROM velia_telegram_offsets WHERE bot_id=%s FOR UPDATE", (bot_id,))
        current = int(cur.fetchone()[0])
        accepted = claim is not None and claim >= current
        if accepted:
            cur.execute("UPDATE velia_telegram_offsets SET next_update=%s WHERE bot_id=%s", (claim + 1, bot_id))
    conn.commit()
    return accepted if claim is not None else current


def conversation(bot_id, user_id, *, reset=False, mode=None):
    # The mapping transaction also serializes creation and checks ownership /
    # deleted history before reusing a conversation. No title-based matching.
    from services import velia_chat_service as chat
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO velia_telegram_chats(bot_id,user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (bot_id, user_id))
            cur.execute("SELECT conversation_id,mode FROM velia_telegram_chats WHERE bot_id=%s AND user_id=%s FOR UPDATE", (bot_id, user_id))
            cid, current_mode = cur.fetchone()
            if reset or not cid or not chat.get_conversation(user_id, cid):
                cid = chat.create_conversation(user_id, "Telegram · VELIA")["id"]
            current_mode = mode or current_mode
            cur.execute("UPDATE velia_telegram_chats SET conversation_id=%s,mode=%s WHERE bot_id=%s AND user_id=%s", (cid, current_mode, bot_id, user_id))
        conn.commit()
        return cid, current_mode
    finally:
        conn.close()


class Runtime:
    def __init__(self, token, session):
        self._token, self.session = token, session
        self.bot_id = int(token.split(":")[0])
        self.tasks, self.active_users = set(), set()
        self.pairing = self.admin = None

    async def api(self, method, **payload):
        if method not in {"getMe", "getWebhookInfo", "getUpdates", "sendMessage", "sendChatAction"}:
            raise ValueError("telegram_method_not_allowed")
        try:
            async with self.session.post(f"https://api.telegram.org/bot{self._token}/{method}",
                    json=payload, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=40)) as response:
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(16384):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise TelegramError()
                    chunks.append(chunk)
                data = json.loads(b"".join(chunks))
                if response.status != 200 or data.get("ok") is not True:
                    raise TelegramError(data.get("error_code", response.status),
                        (data.get("parameters") or {}).get("retry_after", 5))
                return data["result"]
        except TelegramError:
            raise
        except Exception:
            # Request exception messages can contain the bot-token URL.
            raise TelegramError() from None

    async def verify(self):
        identity = await self.api("getMe")
        name = config.configured_username()
        if identity.get("is_bot") is not True or identity.get("id") != self.bot_id or str(identity.get("username", "")).lower() != name.lower():
            raise ValueError("velia_bot_identity_mismatch")
        webhook = await self.api("getWebhookInfo")
        if webhook.get("url"):
            raise ValueError("velia_bot_webhook_already_configured")
        return identity["username"]

    async def send(self, user_id, text, **kwargs):
        # Plain text for model replies; HTML is used only by trusted code templates.
        await self.api("sendMessage", chat_id=user_id, text=text,
            disable_web_page_preview=True, **kwargs)

    def buttons(self):
        rows = [[{"text": "Получить код входа", "url": f"https://t.me/{config.configured_username()}?start=velia_connect"}]]
        if config.web_url():
            rows.insert(0, [{"text": "Открыть VELIA", "url": config.web_url()}])
        return {"inline_keyboard": rows}

    async def handle(self, update):
        raw = update.get("message") or {}
        user, chat = raw.get("from") or {}, raw.get("chat") or {}
        uid = user.get("id")
        if not isinstance(uid, int) or uid <= 0 or user.get("is_bot") or chat.get("type") != "private" or chat.get("id") != uid:
            return
        if time.time() - int(raw.get("date") or 0) > 300:
            return
        if await asyncio.to_thread(is_user_banned, uid):
            await self.send(uid, "Доступ к аккаунту ограничен.")
            return
        text = str(raw.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].split("@")[0].lower() if text else ""
        if command == "/login":
            text = "/start velia_connect"
        if text in {"/start velia_connect", "/start velia_admin_login"} or command == "/start" and len(text.split()) == 2:
            from aiogram.dispatcher.handler import CancelHandler
            from services.velia_telegram_pairing_service import VeliaTelegramPairingMiddleware
            from services.velia_admin_telegram_auth_service import VeliaAdminTelegramAuthMiddleware
            if self.pairing is None:
                self.pairing, self.admin = VeliaTelegramPairingMiddleware(), VeliaAdminTelegramAuthMiddleware()
            message = SimpleNamespace(text=text, from_user=SimpleNamespace(id=uid,
                username=user.get("username", ""), first_name=user.get("first_name", "")),
                chat=SimpleNamespace(type="private"), answer=lambda value, **kw: self.send(uid, value, **{k:v for k,v in kw.items() if k != "disable_web_page_preview"}))
            try:
                await self.admin.on_pre_process_message(message, {})
                await self.pairing.on_pre_process_message(message, {})
            except CancelHandler:
                return
        if command in {"/start", "/help"}:
            await self.send(uid, "Привет! Я Велия. Напиши мне вопрос или получи код входа в VELIA.\n\n/login — код входа\n/new — новый диалог\n/flash — быстрые ответы\n/quantum — сложные вопросы\n/pro — подробная проработка задач", reply_markup=self.buttons())
            return
        from services import velia_chat_service as service
        if not service.is_velia_chat_enabled_for_user(uid):
            await self.send(uid, "Общение с Велией пока недоступно для этого аккаунта.")
            return
        if uid in self.active_users:
            await self.send(uid, "Я ещё готовлю ответ. Дождись завершения, затем продолжим.")
            return
        if not text or command.startswith("/") and command not in {*MODES, "/new"}:
            await self.send(uid, "Пока напиши сообщение текстом. Голос и файлы доступны в веб-версии VELIA.", reply_markup=self.buttons())
            return
        if len(text) > 12000 or len(self.tasks) >= 2:
            await self.send(uid, "Сократи сообщение или попробуй немного позже — сейчас я занята.")
            return
        self.active_users.add(uid)
        task = asyncio.create_task(self.reply(uid, user, text, command, update["update_id"]))
        self.tasks.add(task)
        task.add_done_callback(lambda done: (self.tasks.discard(done), self.active_users.discard(uid)))

    async def reply(self, uid, user, text, command, update_id):
        try:
            await asyncio.to_thread(ensure_user, uid, username=str(user.get("username") or ""),
                first_name=str(user.get("first_name") or ""), source="velia_telegram")
            cid, mode = await asyncio.to_thread(conversation, self.bot_id, uid,
                reset=command == "/new", mode=MODES.get(command))
            if command in MODES or command == "/new":
                await self.send(uid, f"Готова. Режим VELIA {mode.upper()}. Можем продолжать.")
                return
            with suppress(TelegramError):
                await self.api("sendChatAction", chat_id=uid, action="typing")
            from services import velia_chat_service as service
            result = await asyncio.to_thread(service.send_message, uid, cid, text,
                idempotency_key=f"tg:{self.bot_id}:{update_id}", chat_mode=mode)
            answer = (result.get("assistant_message") or {}).get("content")
            if not result.get("ok") or not answer:
                code = str(result.get("error") or "")
                message = "Для PRO нужны токены на балансе." if code in {"pro_tokens_required", "insufficient_tokens"} else "Не удалось получить ответ. Попробуй позже или открой этот диалог в VELIA."
                await self.send(uid, message, reply_markup=self.buttons())
                return
            # UTF-16 length stays below Telegram's limit, including emoji.
            for start in range(0, len(answer), 1900):
                await self.send(uid, answer[start:start + 1900])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("VELIA_TELEGRAM_REPLY_INTERRUPTED")

    async def run(self):
        from services import velia_chat_service as service
        name = await self.verify()
        # Runtime is installed after the complete shared chat pipeline.
        if not callable(getattr(service, "send_message", None)):
            raise ValueError("velia_chat_not_ready")
        conn = None
        key = int.from_bytes(hashlib.sha256(f"velia:telegram:{self.bot_id}".encode()).digest()[:8], "big", signed=True)
        try:
            conn = await asyncio.to_thread(get_connection)
            def initialize():
                ensure_tables(conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
                    locked = cur.fetchone()[0]
                conn.commit()
                return locked
            if not await asyncio.to_thread(initialize):
                return
            config.mark_verified(name)
            logger.info("VELIA_TELEGRAM_READY username=%s", name)
            while True:
                next_update = await asyncio.to_thread(offset, conn, self.bot_id)
                try:
                    updates = await self.api("getUpdates", offset=next_update, timeout=25, limit=10, allowed_updates=["message"])
                except TelegramError as exc:
                    if exc.code in {401, 403, 409}:
                        raise
                    await asyncio.sleep(exc.retry_after)
                    continue
                for update in updates:
                    update_id = update.get("update_id")
                    if not isinstance(update_id, int) or not await asyncio.to_thread(offset, conn, self.bot_id, update_id):
                        continue
                    try:
                        await self.handle(update)
                    except Exception:
                        logger.warning("VELIA_TELEGRAM_UPDATE_INTERRUPTED")
        finally:
            config.mark_verified()
            for task in list(self.tasks):
                task.cancel()
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)
            if conn is not None:
                conn.close()


async def lifecycle(app):
    async def supervise():
        while True:
            try:
                token = config.dedicated_token()
                async with aiohttp.ClientSession(trust_env=False) as session:
                    await Runtime(token, session).run()
            except asyncio.CancelledError:
                raise
            except ValueError:
                logger.warning("VELIA_TELEGRAM_CONFIGURATION_REJECTED")
                return
            except TelegramError as exc:
                logger.warning("VELIA_TELEGRAM_API_UNAVAILABLE status=%s", exc.code)
                if exc.code in {401, 403, 409}:
                    return
                await asyncio.sleep(exc.retry_after)
            except Exception:
                logger.warning("VELIA_TELEGRAM_STORAGE_UNAVAILABLE")
            await asyncio.sleep(5)
    task = None
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "production").lower()
    if config.enabled() and environment in {"production", "prod"}:
        task = asyncio.create_task(supervise())
    try:
        yield
    finally:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
