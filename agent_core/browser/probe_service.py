"""Live VELIA Agent Core browser acceptance using VELIA Flash only."""
import asyncio
import hmac
import json
import os
import secrets
from pathlib import Path

from aiohttp import web

from velia_desktop_routes import setup_velia_desktop_routes


async def run_probe():
    flash_url = os.getenv("VELIA_AGENT_CORE_FLASH_BASE_URL", "").strip()
    flash_key = os.getenv("VELIA_AGENT_CORE_FLASH_API_KEY", "").strip()
    if not flash_url or not flash_key:
        raise RuntimeError("browser_flash_configuration_missing")

    # Reuse the already hardened Flash gateway path, but expose it only on
    # loopback to this one acceptance process.
    os.environ["VELIA_DESKTOP_API_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_ENABLED"] = "true"
    os.environ["VELIA_DESKTOP_FLASH_BASE_URL"] = flash_url
    os.environ["VELIA_DESKTOP_FLASH_API_KEY"] = flash_key
    os.environ["VELIA_DESKTOP_FLASH_CONTEXT_TOKENS"] = os.getenv(
        "VELIA_AGENT_CORE_FLASH_CONTEXT_TOKENS", "8192"
    )

    fixture_id = "velia-agent-core-browser-probe"
    fixture_token = secrets.token_hex(32)
    previous_allowlist = os.environ.get("VELIA_DESKTOP_PREVIEW_USER_IDS")
    os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = fixture_id

    async def authenticate(token):
        return {"user_id": fixture_id} if hmac.compare_digest(token, fixture_token) else None

    app = web.Application()
    setup_velia_desktop_routes(app, authenticate)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    child = None
    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        runtime = os.getenv("VELIA_AGENT_CORE_RUNTIME", "/opt/velia-agent-core/runtime")
        chromium = os.getenv("VELIA_AGENT_CORE_CHROMIUM", "/usr/bin/chromium")
        script = Path(__file__).with_name("probe-live-flash.mjs")
        child = await asyncio.create_subprocess_exec(
            "node",
            str(script),
            f"http://127.0.0.1:{port}",
            fixture_token,
            runtime,
            chromium,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=450)
        output = stdout.decode(errors="replace")
        if child.returncode:
            print("VELIA_AGENT_CORE_BROWSER_FAILURE " + (output + stderr.decode(errors="replace"))[-12000:], flush=True)
            raise RuntimeError("browser_agent_qualification_failed")
        receipt = next(
            (line for line in output.splitlines() if line.startswith("VELIA_AGENT_CORE_BROWSER_PROBE ")),
            None,
        )
        if not receipt:
            raise RuntimeError("browser_agent_receipt_missing")
        payload = json.loads(receipt.split(" ", 1)[1])
        if payload.get("ok") is not True or payload.get("model") != "velia-flash":
            raise RuntimeError("browser_agent_receipt_invalid")
        print(receipt, flush=True)
        return payload
    finally:
        if child and child.returncode is None:
            child.kill()
            await child.wait()
        await runner.cleanup()
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
