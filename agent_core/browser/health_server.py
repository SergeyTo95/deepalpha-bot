"""Private VELIA Agent Core Browser service.

The public Railway surface exposes health only. Browser tasks require a separate
internal bearer key and run through VELIA Flash with no paid-model fallback.
"""
import asyncio
import hmac
import json
import os
import secrets
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from velia_desktop_routes import setup_velia_desktop_routes

MAX_RUN_BODY = 16 * 1024
MAX_PROMPT_CHARS = 8000
RUN_TIMEOUT_SECONDS = 450
RUN_CONCURRENCY = 1

fixture_id = "velia-agent-core-browser-service"
fixture_token = secrets.token_hex(32)
run_sem = asyncio.Semaphore(RUN_CONCURRENCY)


def _configure_flash_gateway():
    base = os.getenv("VELIA_AGENT_CORE_FLASH_BASE_URL", "").strip()
    key = os.getenv("VELIA_AGENT_CORE_FLASH_API_KEY", "").strip()
    os.environ["VELIA_DESKTOP_API_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_BASE_URL"] = base
    os.environ["VELIA_DESKTOP_FLASH_API_KEY"] = key
    os.environ["VELIA_DESKTOP_FLASH_CONTEXT_TOKENS"] = os.getenv(
        "VELIA_AGENT_CORE_FLASH_CONTEXT_TOKENS", "8192"
    )
    os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = fixture_id
    return base, key


FLASH_BASE_URL, FLASH_API_KEY = _configure_flash_gateway()


async def authenticate(token):
    return {"user_id": fixture_id} if hmac.compare_digest(token, fixture_token) else None


def _internal_authorized(request):
    expected = os.getenv("VELIA_AGENT_CORE_INTERNAL_KEY", "").strip()
    header = request.headers.get("Authorization", "")
    if not expected or not header.startswith("Bearer "):
        return False
    supplied = header[7:]
    return hmac.compare_digest(supplied, expected)


async def _warm_flash():
    if not FLASH_BASE_URL or not FLASH_API_KEY:
        raise RuntimeError("browser_flash_configuration_missing")
    timeout = ClientTimeout(total=5)
    async with ClientSession(timeout=timeout) as client:
        for _ in range(60):
            try:
                async with client.get(FLASH_BASE_URL.rstrip("/") + "/health", allow_redirects=False) as response:
                    if response.status == 200:
                        return
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)
    raise RuntimeError("browser_flash_startup_timeout")


async def health(_request):
    return web.json_response({
        "ok": True,
        "service": "velia-agent-core-browser",
        "model": "velia-flash",
        "browser": "playwright-mcp",
        "public_agent": False,
        "run_api_configured": bool(os.getenv("VELIA_AGENT_CORE_INTERNAL_KEY", "").strip()),
        "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA", ""),
    })


async def run_browser_task(request):
    if not _internal_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    if request.content_length is not None and request.content_length > MAX_RUN_BODY:
        return web.json_response({"ok": False, "error": "request_too_large"}, status=413)

    body = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        body.extend(chunk)
        if len(body) > MAX_RUN_BODY:
            return web.json_response({"ok": False, "error": "request_too_large"}, status=413)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        return web.json_response({"ok": False, "error": "invalid_prompt"}, status=400)

    if run_sem.locked():
        return web.json_response({"ok": False, "error": "browser_agent_busy"}, status=429)

    child = None
    async with run_sem:
        try:
            await _warm_flash()
            runtime = os.getenv("VELIA_AGENT_CORE_RUNTIME", "/opt/velia-agent-core/runtime")
            chromium = os.getenv("VELIA_AGENT_CORE_CHROMIUM", "/usr/bin/chromium")
            script = Path(__file__).with_name("run_task.mjs")
            port = int(os.getenv("PORT", "8080"))
            child = await asyncio.create_subprocess_exec(
                "node",
                str(script),
                f"http://127.0.0.1:{port}",
                fixture_token,
                runtime,
                chromium,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
            )
            stdout, stderr = await asyncio.wait_for(
                child.communicate(prompt.encode("utf-8")),
                timeout=RUN_TIMEOUT_SECONDS,
            )
            if child.returncode != 0:
                message = stderr.decode(errors="replace")[-2000:]
                print("VELIA_AGENT_CORE_BROWSER_RUN_FAILED returncode="
                      f"{child.returncode} detail={message!r}", flush=True)
                return web.json_response({"ok": False, "error": "browser_agent_failed"}, status=502)
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeDecodeError):
                return web.json_response({"ok": False, "error": "browser_agent_invalid_result"}, status=502)
            if result.get("ok") is not True or result.get("model") != "velia-flash":
                return web.json_response({"ok": False, "error": "browser_agent_invalid_result"}, status=502)
            print("VELIA_AGENT_CORE_BROWSER_RUN_OK "
                  + json.dumps({
                      "tool_count": result.get("tool_count", 0),
                      "distinct_tools": result.get("distinct_tools", 0),
                      "answer_chars": len(result.get("text") or ""),
                  }, sort_keys=True), flush=True)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except asyncio.TimeoutError:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            return web.json_response({"ok": False, "error": "browser_agent_timeout"}, status=504)
        except (OSError, RuntimeError) as exc:
            print(f"VELIA_AGENT_CORE_BROWSER_RUN_ERROR code={str(exc)[:120]!r}", flush=True)
            return web.json_response({"ok": False, "error": "browser_agent_unavailable"}, status=503)
        except asyncio.CancelledError:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            raise


app = web.Application(client_max_size=MAX_RUN_BODY)
app.router.add_get("/health", health)
app.router.add_post("/v1/run", run_browser_task)
# Private loopback model route used only by the child Agent Core process. It is
# still token-authenticated, and the random token never leaves this container.
setup_velia_desktop_routes(app, authenticate)


if __name__ == "__main__":
    web.run_app(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        access_log=None,
        handler_cancellation=True,
    )
