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

    app = web.Application()
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
        browser = await asyncio.create_subprocess_exec(
            chromium,
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "about:blank",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        debug_port = await _wait_for_debug_port(profile, browser)
        endpoint = f"http://127.0.0.1:{debug_port}"
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
                "Открой https://example.com. Прочитай заголовок страницы и первый абзац. "
                "Ответь одной короткой фразой по-русски. Обязательно используй браузерный инструмент."
            ),
        )
        second = await _run_turn(
            script,
            gateway,
            fixture_token,
            runtime,
            endpoint,
            root,
            first["session_id"],
            (
                "Продолжи в уже открытом браузере. Никуда больше не переходи. "
                "Через браузерный инструмент проверь текущую вкладку и назови точный URL "
                "и заголовок страницы одной короткой фразой."
            ),
        )

        if first["session_id"] != second["session_id"]:
            raise RuntimeError("browser_agent_session_not_resumed")
        if "example.com" not in str(second["text"]).lower():
            raise RuntimeError("browser_agent_current_page_not_preserved")

        receipt = {
            "ok": True,
            "model": "velia-flash",
            "browser": "playwright-mcp-attach",
            "turns": 2,
            "persistent_agent_session": True,
            "current_page_preserved": True,
            "browser_tool_used_each_turn": True,
            "tool_count": int(first["tool_count"]) + int(second["tool_count"]),
            "paid_fallback": False,
        }
        print(
            "VELIA_AGENT_CORE_BROWSER_PROBE " + json.dumps(receipt, sort_keys=True),
            flush=True,
        )
        return receipt
    finally:
        if browser and browser.returncode is None:
            browser.terminate()
            try:
                await asyncio.wait_for(browser.wait(), timeout=5)
            except asyncio.TimeoutError:
                browser.kill()
                await browser.wait()
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
