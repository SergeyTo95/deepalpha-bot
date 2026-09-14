"""Real HTTP compatibility checks for the patched server/client dependency."""
import asyncio
import io

import aiogram
import aiohttp
import pytest
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.bot.api import TelegramAPIServer
from aiogram.utils.exceptions import RetryAfter

from services.aiohttp_handler_cancellation_service import handler_cancellation_run_app_kwargs


def test_legacy_bot_constructs_before_event_loop():
    bot = Bot("123456:local-test-token")
    assert bot.id == 123456
    assert bot._session is None


def test_native_cancellation_is_used_with_current_aiohttp():
    assert handler_cancellation_run_app_kwargs(web) == {"handler_cancellation": True}


def test_real_bot_http_roundtrip_upload_download_dispatch_and_shutdown():
    async def scenario():
        seen = []
        received = []

        async def api(request):
            method = request.match_info["method"]
            if request.content_type == "multipart/form-data":
                reader = await request.multipart()
                fields = {}
                while part := await reader.next():
                    fields[part.name] = await part.read()
                assert bytes(fields["document"]) == b"document-bytes"
                assert bytes(fields["chat_id"]) == b"7"
            else:
                fields = dict(await request.post())
            seen.append((method, fields))
            if method == "getMe":
                result = {"id": 123456, "is_bot": True, "first_name": "Local Test", "username": "local_test_bot"}
            elif method == "getUpdates":
                result = [{"update_id": 11, "message": {
                    "message_id": 1, "date": 1700000000,
                    "chat": {"id": 7, "type": "private"},
                    "from": {"id": 7, "is_bot": False, "first_name": "Tester"},
                    "text": "hello",
                }}]
            elif method == "sendDocument":
                result = {"message_id": 2, "date": 1700000000, "chat": {"id": 7, "type": "private"}}
            elif method == "sendMessage":
                return web.json_response(
                    {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 2}},
                    status=429,
                )
            else:
                raise AssertionError(method)
            return web.json_response({"ok": True, "result": result})

        async def download(request):
            return web.Response(body=b"downloaded-bytes")

        app = web.Application()
        app.router.add_post("/bot{token}/{method}", api)
        app.router.add_get("/file/bot{token}/{path}", download)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        bot = Bot("123456:local-test-token", server=TelegramAPIServer.from_base(f"http://127.0.0.1:{port}"))
        dispatcher = Dispatcher(bot)

        async def message_handler(message):
            received.append(message.text)

        dispatcher.register_message_handler(message_handler)
        try:
            assert (await bot.get_me()).id == 123456
            updates = await bot.get_updates(timeout=1)
            assert len(updates) == 1
            Bot.set_current(bot)
            Dispatcher.set_current(dispatcher)
            await dispatcher.process_updates(updates)
            assert received == ["hello"]
            uploaded = await bot.send_document(7, types.InputFile(io.BytesIO(b"document-bytes"), filename="test.txt"))
            assert uploaded.message_id == 2
            content = await bot.download_file("test.txt")
            assert content.getvalue() == b"downloaded-bytes"
            with pytest.raises(RetryAfter):
                await bot.send_message(7, "local HTTP test")
            session = await bot.get_session()
            assert not session.closed
        finally:
            await bot.close()
            await dispatcher.storage.close()
            await dispatcher.storage.wait_closed()
            await runner.cleanup()
        assert session.closed
        assert {method for method, _ in seen} == {"getMe", "getUpdates", "sendDocument", "sendMessage"}

    asyncio.run(scenario())
