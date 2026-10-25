"""Local browser QA only. Never included in the public gateway image."""
import asyncio
import base64
import json
import os
import uuid
import tempfile
from aiohttp import web
from desktop.gateway import GatewayConfig, create_app
from desktop.guest_store import GuestStore
from desktop.web_search import WebSearch


async def main():
    calls = []
    credits = 0
    user_id = 7
    old_id = "11111111-1111-1111-1111-111111111111"
    conversations = {old_id: {"conversation": {"id": old_id, "title": "Старый диалог из приложения", "updated_at": "2026-10-01T19:00:00Z"},
        "messages": [{"role": "user", "content": "Моя прежняя идея", "status": "completed"},
            {"role": "assistant", "content": "Я помню твою идею", "status": "completed", "chat_mode": "flash"}]}}
    requests = {}
    uploaded = {}
    access, refresh = "va_" + "a" * 48, "vr_" + "r" * 48
    async def health(request):
        return web.json_response({"ok": True, "enabled": True})
    async def me(request):
        return web.json_response({"ok": True, "user": {"id": user_id}, "features": {"chat_selected_model_inputs": True, "chat_quantum": os.getenv("VELIA_FIXTURE_QUANTUM") == "true", "chat_quantum_attachments": os.getenv("VELIA_FIXTURE_QUANTUM") == "true", "chat_quantum_source": "GLM-5.3 через Experiential"}}) if request.headers.get("Authorization") == "Bearer " + access else web.json_response({"ok": False}, status=401)
    async def auth(request):
        return web.json_response({"ok": True, "access_token": access, "refresh_token": refresh, "access_expires_in": 900})
    async def logout(request):
        return web.json_response({"ok": True})
    async def economy(request):
        return web.json_response({"ok": True, "account": {"credits": credits}})
    async def fixture_credits(request):
        nonlocal credits
        credits = (await request.json())["credits"]
        return web.json_response({"ok": True})
    async def fixture_user(request):
        nonlocal user_id
        user_id = (await request.json())["user_id"]
        return web.json_response({"ok": True})
    async def fixture_search(request):
        await request.json()
        return web.json_response({"results":[
            {"title":"Python official", "url":"https://www.python.org/downloads/", "content":"Python fixture release from the official website."},
            {"title":"Python docs", "url":"https://docs.python.org/", "content":"Official documentation."}]})
    async def stored_conversations(request):
        if request.method == "GET":
            return web.json_response({"ok": True, "conversations": [c["conversation"] for c in conversations.values()]})
        data = await request.json()
        value = {"id": str(uuid.uuid4()), "title": data["title"], "updated_at": "2026-10-03T20:00:00Z"}
        conversations[value["id"]] = {"conversation": value, "messages": []}
        return web.json_response({"ok": True, "conversation": value}, status=201)
    async def stored_messages(request):
        value = conversations.get(request.match_info["conversation_id"])
        if not value:
            return web.json_response({"ok": False}, status=404)
        return web.json_response({"ok": True, "messages": value["messages"]})
    async def stored_delete(request):
        conversations.pop(request.match_info["conversation_id"], None)
        return web.json_response({"ok": True})
    async def stored_send(request):
        data = await request.json()
        value = conversations[request.match_info["conversation_id"]]
        text = "Я Велия. **Готова помочь** с твоей идеей.\n\n```python\nprint('VELIA')\n```"
        duplicate = data["idempotency_key"] in requests
        if not duplicate:
            calls.append(data["chat_mode"])
            answer = {"role": "assistant", "content": text, "chat_mode": data["chat_mode"], "status": "completed"}
            value["messages"].extend([{"role": "user", "content": data["content"], "status": "completed",
                "attachments": [uploaded[key] for key in data.get("attachment_ids", [])]}, answer])
            requests[data["idempotency_key"]] = answer
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            await response.write(b'data: {"type":"ready"}\n\n')
            if not duplicate:
                for piece in (text[:16], text[16:]):
                    await response.write(("data: " + json.dumps({"type": "delta", "text": piece}) + "\n\n").encode())
                    await asyncio.sleep(15 if "останов" in data["content"].lower() else 0.1)
            await response.write(("data: " + json.dumps({"type": "complete", "result": {"ok": True, "assistant_message": requests[data["idempotency_key"]]}}) + "\n\n").encode())
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response
    async def template(request):
        return web.json_response({"prompt": "fixture"})
    async def upload(request):
        reader = await request.multipart()
        field = await reader.next()
        await field.read()
        item = {"id": str(uuid.uuid4()), "name": field.filename,
            "mime_type": field.headers.get("Content-Type", "text/plain"), "kind": "document", "status": "ready",
            "processing_mode": request.headers.get("X-Velia-Chat-Mode")}
        uploaded[item["id"]] = item
        return web.json_response({"ok": True, "attachment": item}, status=201)
    async def tokenize(request):
        return web.json_response({"tokens": [1, 2, 3]})
    async def model(request):
        payload = await request.json()
        tool = payload.get("tools", [{}])[0].get("function", {}).get("name")
        if tool == "understand_request":
            args = {"action": "search", "quote": "", "query": "Python fixture"}
            return web.json_response({"choices": [{"message": {"tool_calls": [{"function": {
                "name": tool, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]})
        if tool == "publish_reviewed_answer":
            args = {"paragraphs": [{"text": "Я Велия. **Готова помочь** с твоей идеей.\n\n```python\nprint('VELIA')\n```", "source_ids": [1]}]}
            return web.json_response({"choices": [{"message": {"tool_calls": [{"function": {
                "name": tool, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]})
        calls.append(payload["model"])
        slow = "останов" in payload["messages"][-1]["content"].lower()
        text = "Я Велия. **Готова помочь** с твоей идеей.\n\n```python\nprint('VELIA')\n```"
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            for piece in (text[:16], text[16:]):
                data = {"choices": [{"delta": {"content": piece}}]}
                await response.write(("data: " + json.dumps(data) + "\n\n").encode())
                await asyncio.sleep(15 if slow else 0.1)
            await response.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            await response.write_eof()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response
    authority = web.Application()
    authority.router.add_get("/mobile-api/v1/health", health)
    authority.router.add_get("/mobile-api/v1/me", me)
    authority.router.add_get("/mobile-api/v1/economy/me", economy)
    authority.router.add_post("/__fixture/credits", fixture_credits)
    authority.router.add_post("/__fixture/user", fixture_user)
    authority.router.add_post("/__fixture/search", fixture_search)
    authority.router.add_get("/mobile-api/v1/conversations", stored_conversations)
    authority.router.add_post("/mobile-api/v1/conversations", stored_conversations)
    authority.router.add_get("/mobile-api/v1/conversations/{conversation_id}/messages", stored_messages)
    authority.router.add_delete("/mobile-api/v1/conversations/{conversation_id}", stored_delete)
    authority.router.add_post("/mobile-api/v1/conversations/{conversation_id}/messages/stream", stored_send)
    authority.router.add_post("/mobile-api/v1/conversations/{conversation_id}/attachments", upload)
    authority.router.add_post("/mobile-api/v1/conversations/{conversation_id}/attachments/selected", upload)
    authority.router.add_get("/__fixture/uploads", lambda request: web.json_response({"modes":[item["processing_mode"] for item in uploaded.values()]}))
    authority.router.add_post("/mobile-api/v1/auth/exchange", auth)
    authority.router.add_post("/mobile-api/v1/auth/refresh", auth)
    authority.router.add_post("/mobile-api/v1/auth/logout", logout)
    authority.router.add_post("/v1/chat/completions", model)
    authority.router.add_post("/apply-template", template)
    authority.router.add_post("/tokenize", tokenize)
    runner = web.AppRunner(authority, access_log=None, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 18181)
    await site.start()
    os.environ.update(VELIA_WEB_ENABLED="true", VELIA_WEB_ORIGIN="https://fixture.invalid",
        VELIA_WEB_SESSION_KEY=base64.urlsafe_b64encode(b"t" * 32).decode(),
        VELIA_DESKTOP_API_ENABLED="true", VELIA_DESKTOP_PREVIEW_USER_IDS="7,8",
        KIMI_API_KEY="fixture", KIMI_BASE_URL="http://127.0.0.1:18181/v1",
        VELIA_DESKTOP_FLASH_ENABLED="true", VELIA_DESKTOP_FLASH_API_KEY="fixture",
        VELIA_DESKTOP_FLASH_BASE_URL="http://127.0.0.1:18181")
    os.environ["VELIA_WEB_GUEST_ENABLED"] = "true"
    if os.getenv("VELIA_FIXTURE_QUANTUM") == "true":
        os.environ.update(VELIA_QUANTUM_ENABLED="true", VELIA_QUANTUM_PROVIDER="self_hosted",
            VELIA_QUANTUM_BASE_URL="http://127.0.0.1:18181/v1", VELIA_QUANTUM_MODEL="fixture-quantum",
            VELIA_QUANTUM_API_KEY="fixture-quantum-key")
    temp = tempfile.TemporaryDirectory(prefix="velia-guest-fixture-")
    store = GuestStore(sqlite_path=temp.name + "/guest.sqlite")
    search = WebSearch(provider="tavily", api_key="fixture-search-key",
        endpoint="http://127.0.0.1:18181/__fixture/search", store=store)
    app = create_app(GatewayConfig("http://127.0.0.1:18181", "https://deepalpha-ai.com"), web_origin="http://127.0.0.1:18180",
        guest_store=store, web_search=search)
    gateway = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await gateway.setup()
    await web.TCPSite(gateway, "127.0.0.1", 18180).start()
    print("VELIA_WEB_FIXTURE_READY", flush=True)
    await asyncio.Event().wait()


asyncio.run(main())
