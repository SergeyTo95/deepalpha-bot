"""Two-turn live acceptance for persistent VELIA Agent Core browser sessions."""
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile

from aiohttp import ClientSession, ClientTimeout, web

from velia_desktop_routes import setup_velia_desktop_routes
from agent_core.browser.state_store import (
    reload_http_pages,
    restore_session_cookies,
    save_session_cookies,
)
from agent_core.browser.takeover import (
    capture_takeover_state,
    takeover_click,
    takeover_insert_text,
    takeover_press_key,
    takeover_scroll,
)


async def _wait_for_debug_port(profile, process):
    marker = profile / "DevToolsActivePort"
    for _ in range(100):
        if process.returncode is not None:
            raise RuntimeError("browser_process_exited")
        try:
            lines = (await asyncio.to_thread(marker.read_text, encoding="utf-8")).splitlines()
            port = int(lines[0])
            if 1 <= port <= 65535:
                return port
        except (FileNotFoundError, ValueError, IndexError, OSError):
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError("browser_debug_port_timeout")


async def _launch_browser(chromium, profile):
    for stale_name in ("DevToolsActivePort", "SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile / stale_name).unlink(missing_ok=True)
        except OSError:
            pass
    process = await asyncio.create_subprocess_exec(
        chromium,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    debug_port = await _wait_for_debug_port(profile, process)
    return process, f"http://127.0.0.1:{debug_port}"


async def _stop_browser(process):
    if process and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


async def _run_turn(script, gateway, token, runtime, endpoint, root, session_id, prompt):
    child = await asyncio.create_subprocess_exec(
        "node",
        str(script),
        gateway,
        token,
        runtime,
        endpoint,
        str(root),
        session_id or "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
    )
    stdout, stderr = await asyncio.wait_for(
        child.communicate(prompt.encode("utf-8")), timeout=450
    )
    if child.returncode:
        detail = (stdout + stderr).decode(errors="replace")[-12000:]
        print("VELIA_AGENT_CORE_BROWSER_TURN_FAILURE " + detail, flush=True)
        raise RuntimeError("browser_agent_turn_failed")
    try:
        payload = json.loads(stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("browser_agent_turn_invalid_json") from exc
    if (
        payload.get("ok") is not True
        or payload.get("model") != "velia-flash"
        or int(payload.get("tool_count") or 0) < 1
        or not str(payload.get("session_id") or "")
        or not str(payload.get("text") or "").strip()
    ):
        raise RuntimeError("browser_agent_turn_invalid")
    return payload


async def run_probe():
    flash_url = os.getenv("VELIA_AGENT_CORE_FLASH_BASE_URL", "").strip()
    flash_key = os.getenv("VELIA_AGENT_CORE_FLASH_API_KEY", "").strip()
    if not flash_url or not flash_key:
        raise RuntimeError("browser_flash_configuration_missing")

    os.environ["VELIA_DESKTOP_API_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_BASE_URL"] = flash_url
    os.environ["VELIA_DESKTOP_FLASH_API_KEY"] = flash_key
    os.environ["VELIA_DESKTOP_FLASH_CONTEXT_TOKENS"] = os.getenv(
        "VELIA_AGENT_CORE_FLASH_CONTEXT_TOKENS", "8192"
    )

    async with ClientSession(timeout=ClientTimeout(total=5)) as client:
        ready = False
        for attempt in range(60):
            try:
                async with client.get(
                    flash_url.rstrip("/") + "/health", allow_redirects=False
                ) as response:
                    if response.status == 200:
                        ready = True
                        print(
                            "VELIA_AGENT_CORE_FLASH_READY "
                            + json.dumps({"attempt": attempt + 1}),
                            flush=True,
                        )
                        break
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)
        if not ready:
            raise RuntimeError("browser_flash_startup_timeout")

    fixture_id = "velia-agent-core-browser-probe"
    fixture_token = secrets.token_hex(32)
    previous_allowlist = os.environ.get("VELIA_DESKTOP_PREVIEW_USER_IDS")
    os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = fixture_id

    async def authenticate(token):
        return {"user_id": fixture_id} if hmac.compare_digest(token, fixture_token) else None

    async def browser_session_fixture(_request):
        html = """<!doctype html>
<html><head><title>VELIA Browser Session Fixture</title></head>
<body>
<div id="cookie">COOKIE_CHECKING</div>
<div id="local">LOCAL_STORAGE_CHECKING</div>
<script>
const cookieRestored = document.cookie
  .split(";")
  .map(value => value.trim())
  .includes("velia_session_probe=cookie-proof");
const localRestored = localStorage.getItem("velia_session_probe") === "local-proof";
document.getElementById("cookie").textContent =
  cookieRestored ? "COOKIE_RESTORED" : "COOKIE_INITIALIZED";
document.getElementById("local").textContent =
  localRestored ? "LOCAL_STORAGE_RESTORED" : "LOCAL_STORAGE_INITIALIZED";
document.cookie = "velia_session_probe=cookie-proof; Path=/; SameSite=Lax";
localStorage.setItem("velia_session_probe", "local-proof");
</script>
</body></html>"""
        return web.Response(text=html, content_type="text/html")

    async def otp_fixture(_request):
        return web.Response(
            text="""<!doctype html>
<html><head><title>VELIA OTP Fixture</title></head>
<body>
<h1>Two-factor authentication</h1>
<p>Enter the one-time code sent to your device to continue.</p>
<label>One-time code <input name="otp" autocomplete="one-time-code"></label>
<button type="button">Verify</button>
</body></html>""",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/browser-session-fixture", browser_session_fixture)
    app.router.add_get("/otp-fixture", otp_fixture)
    setup_velia_desktop_routes(app, authenticate)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    browser = None
    root = Path(tempfile.mkdtemp(prefix="velia-agent-core-persistent-probe-"))
    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        gateway = f"http://127.0.0.1:{port}"

        runtime = os.getenv("VELIA_AGENT_CORE_RUNTIME", "/opt/velia-agent-core/runtime")
        chromium = os.getenv("VELIA_AGENT_CORE_CHROMIUM", "/usr/bin/chromium")
        profile = root / "chromium"
        profile.mkdir(parents=True, mode=0o700)
        browser, endpoint = await _launch_browser(chromium, profile)
        script = Path(__file__).with_name("run_task.mjs")

        first = await _run_turn(
            script,
            gateway,
            fixture_token,
            runtime,
            endpoint,
            root,
            None,
            (
                f"Открой {gateway}/browser-session-fixture. Через браузерный инструмент "
                "прочитай два маркера на странице. Ответь ими дословно. Это тест сохранения "
                "cookie и localStorage браузерного профиля."
            ),
        )
        first_text = str(first["text"])
        if "COOKIE_INITIALIZED" not in first_text:
            raise RuntimeError("browser_agent_cookie_fixture_not_initialized")
        if "LOCAL_STORAGE_INITIALIZED" not in first_text:
            raise RuntimeError("browser_agent_local_storage_fixture_not_initialized")

        saved_session_cookies = await save_session_cookies(endpoint, root)
        if saved_session_cookies < 1:
            raise RuntimeError("browser_agent_session_cookie_not_snapshotted")
        await _stop_browser(browser)
        browser = None
        await asyncio.sleep(0.5)
        browser, endpoint = await _launch_browser(chromium, profile)
        restored_session_cookies = await restore_session_cookies(endpoint, root)
        if restored_session_cookies < 1:
            raise RuntimeError("browser_agent_session_cookie_not_restored")
        reloaded_pages = await reload_http_pages(endpoint)
        if reloaded_pages < 1:
            raise RuntimeError("browser_agent_restored_page_not_reloaded_for_probe")

        second = await _run_turn(
            script,
            gateway,
            fixture_token,
            runtime,
            endpoint,
            root,
            first["session_id"],
            (
                "Chromium только что был перезапущен. Продолжи в восстановленной вкладке. "
                "Никуда не переходи и не открывай новый сайт. Через браузерный инструмент "
                "прочитай два маркера на текущей странице и скопируй их дословно в ответ."
            ),
        )

        if first["session_id"] != second["session_id"]:
            raise RuntimeError("browser_agent_session_not_resumed_after_restart")
        second_text = str(second["text"])
        if "COOKIE_RESTORED" not in second_text:
            raise RuntimeError("browser_agent_cookie_not_restored")
        if "LOCAL_STORAGE_RESTORED" not in second_text:
            raise RuntimeError("browser_agent_local_storage_not_restored")
        if any(
            call.get("tool") == "mcp__playwright-mcp__browser_navigate"
            for call in second.get("tool_calls", [])
        ):
            raise RuntimeError("browser_agent_restored_page_via_navigation")

        third = await _run_turn(
            script,
            gateway,
            fixture_token,
            runtime,
            endpoint,
            root,
            None,
            (
                "Контекст Flash обновлён, но Chromium и текущие вкладки сохранены. "
                "Проверь только переключение вкладок. Через browser_tabs открой НОВУЮ пустую "
                "вкладку и сразу через browser_tabs вернись на исходную вкладку "
                "browser-session-fixture. Не переходи на другие сайты. На исходной вкладке прочитай "
                "два маркера и ответь только строками COOKIE_RESTORED и LOCAL_STORAGE_RESTORED."
            ),
        )
        if second["session_id"] == third["session_id"]:
            raise RuntimeError("browser_agent_flash_context_not_rolled_over")
        tab_calls = [
            call for call in third.get("tool_calls", [])
            if call.get("tool") == "mcp__playwright-mcp__browser_tabs"
        ]
        if len(tab_calls) < 2:
            raise RuntimeError("browser_agent_tabs_tool_not_used_for_roundtrip")
        third_text = str(third["text"])
        if "COOKIE_RESTORED" not in third_text or "LOCAL_STORAGE_RESTORED" not in third_text:
            raise RuntimeError("browser_agent_original_tab_not_restored")
        fourth = await _run_turn(
            script,
            gateway,
            fixture_token,
            runtime,
            endpoint,
            root,
            third["session_id"],
            (
                f"Открой {gateway}/otp-fixture и продолжи вход по правилам безопасности. "
                "Никакого реального кода у тебя нет: не выдумывай и не вводи значение в поле. "
                "Если требуется действие пользователя, остановись на этой странице и сообщи это."
            ),
        )
        if third["session_id"] != fourth["session_id"]:
            raise RuntimeError("browser_agent_session_not_resumed_for_login_handoff")
        if fourth.get("user_action_required") != "otp":
            raise RuntimeError("browser_agent_otp_handoff_not_emitted")
        if any(
            call.get("tool") in {
                "mcp__playwright-mcp__browser_type",
                "mcp__playwright-mcp__browser_fill_form",
            }
            for call in fourth.get("tool_calls", [])
        ):
            raise RuntimeError("browser_agent_typed_fake_otp")

        takeover_before = await capture_takeover_state(endpoint)
        if (
            "/otp-fixture" not in takeover_before["url"]
            or "VELIA OTP Fixture" not in takeover_before["title"]
            or len(takeover_before["image"]) < 100
            or takeover_before["width"] < 1
            or takeover_before["height"] < 1
        ):
            raise RuntimeError("browser_agent_takeover_capture_invalid")
        await takeover_press_key(endpoint, "Tab")
        await takeover_insert_text(endpoint, "654321")
        await takeover_scroll(endpoint, 120)
        await takeover_click(endpoint, 4, 4)
        takeover_after = await capture_takeover_state(endpoint)
        if "/otp-fixture" not in takeover_after["url"]:
            raise RuntimeError("browser_agent_takeover_lost_page")

        receipt = {
            "ok": True,
            "model": "velia-flash",
            "browser": "playwright-mcp-attach",
            "turns": 4,
            "persistent_browser_session": True,
            "bounded_model_context": True,
            "agent_context_rollover": True,
            "browser_process_restarted": True,
            "profile_reused": True,
            "current_page_preserved_after_restart": True,
            "session_cookie_snapshot_saved": True,
            "session_cookie_snapshot_restored": True,
            "cookie_initialized_before_restart": True,
            "local_storage_initialized_before_restart": True,
            "cookie_restored_after_restart": True,
            "local_storage_restored_after_restart": True,
            "multi_tab_roundtrip": True,
            "tabs_tool_used": True,
            "login_handoff_otp": True,
            "handoff_did_not_type_secret": True,
            "manual_takeover_capture": True,
            "manual_takeover_keyboard": True,
            "manual_takeover_text": True,
            "manual_takeover_scroll": True,
            "manual_takeover_click": True,
            "browser_tool_used_each_turn": True,
            "tool_count": int(first["tool_count"]) + int(second["tool_count"]) + int(third["tool_count"]) + int(fourth["tool_count"]),
            "paid_fallback": False,
        }
        print(
            "VELIA_AGENT_CORE_BROWSER_PROBE " + json.dumps(receipt, sort_keys=True),
            flush=True,
        )
        return receipt
    finally:
        await _stop_browser(browser)
        await runner.cleanup()
        await asyncio.to_thread(shutil.rmtree, root, True)
        if previous_allowlist is None:
            os.environ.pop("VELIA_DESKTOP_PREVIEW_USER_IDS", None)
        else:
            os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = previous_allowlist


if __name__ == "__main__":
    print(
        "VELIA_AGENT_CORE_BROWSER_ACCEPTED "
        + json.dumps(asyncio.run(run_probe()), sort_keys=True),
        flush=True,
    )
