"""Explicit operator-only live check; this is not a public authentication bypass.

Two bounded model calls check tool correlation and SSE. The tool supplies a
fixed test value, not user data. This does not claim owner-account pairing.
"""
import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, web
from desktop.gateway import GatewayConfig
from velia_desktop_routes import check_flash_context, flash_enabled, flash_endpoint, setup_velia_desktop_routes, validate_payload


async def run_flash_probe():
    """Use shipped Harness and a live worker in this private pre-deploy process.

    The fixture identity is unrelated to any user and listens on loopback only.
    This server and its synthetic allowlist never run in the public gateway.
    """
    if not flash_enabled():
        raise RuntimeError("flash_configuration_unavailable")
    # A small request separates a template/runtime problem from Harness input.
    payload = validate_payload({"model": "velia-flash", "max_tokens": 128, "messages": [
        {"role": "system", "content": "You are VELIA. Call read_probe to read a verification value."},
        {"role": "user", "content": "Read the verification value using read_probe."}],
        "tools": [{"type": "function", "function": {"name": "read_probe", "description": "Read a fixed verification value.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}], "tool_choice": "required"})
    headers = {"Authorization": "Bearer " + os.environ["VELIA_DESKTOP_FLASH_API_KEY"].strip()}
    endpoint = flash_endpoint()
    async with ClientSession(timeout=ClientTimeout(total=180, sock_read=150), cookie_jar=DummyCookieJar()) as client:
        await check_flash_context(client, endpoint, headers, payload)
        async with client.post(endpoint + "/v1/chat/completions", json=payload, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError("flash_tool_status_" + str(response.status))
            result = await response.json()
        message = result["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        print("VELIA_FLASH_OPERATOR_TOOL " + json.dumps({"ok": len(calls) == 1,
            "tool_names": [c.get("function", {}).get("name") for c in calls], "content_characters": len(message.get("content") or ""),
            "finish_reason": result["choices"][0].get("finish_reason")}), flush=True)
        if len(calls) != 1 or calls[0]["function"]["name"] != "read_probe":
            raise RuntimeError("flash_tool_call_missing")
    fixture_id = "desktop-operator-probe"
    fixture_token = secrets.token_hex(32)
    async def authenticate(token):
        return {"user_id": fixture_id} if hmac.compare_digest(token, fixture_token) else None
    app = web.Application()
    setup_velia_desktop_routes(app, authenticate)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    previous = os.environ.get("VELIA_DESKTOP_PREVIEW_USER_IDS")
    os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = fixture_id
    child = None
    try:
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        runtime = os.getenv("VELIA_DESKTOP_QUALIFICATION_RUNTIME", "/opt/velia-qualification/harness")
        script = Path(__file__).parent / "scripts" / "probe-live-flash.mjs"
        child = await asyncio.create_subprocess_exec("node", str(script), f"http://127.0.0.1:{port}",
            fixture_token, runtime, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"})
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=570)
        if child.returncode:
            # Child has no provider or account credentials. Keep failures bounded.
            print("VELIA_FLASH_HARNESS_FAILURE " + (stdout + stderr).decode(errors="replace")[-8000:], flush=True)
            raise RuntimeError("flash_harness_qualification_failed")
        receipt = next((line for line in stdout.decode().splitlines()
                        if line.startswith("VELIA_FLASH_HARNESS_PROBE ")), None)
        if not receipt or json.loads(receipt.split(" ", 1)[1]).get("ok") is not True:
            raise RuntimeError("flash_harness_receipt_missing")
        print(receipt, flush=True)
        return {"flash_live_harness": True, "flash_paid_fallback": False}
    finally:
        if child and child.returncode is None:
            child.kill()
            await child.wait()
        await runner.cleanup()
        if previous is None:
            os.environ.pop("VELIA_DESKTOP_PREVIEW_USER_IDS", None)
        else:
            os.environ["VELIA_DESKTOP_PREVIEW_USER_IDS"] = previous


async def run_probe():
    config = GatewayConfig.from_env()
    key = os.environ["KIMI_API_KEY"].strip()
    base = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("invalid_provider_address")
    headers = {"Authorization": "Bearer " + key}
    marker = "VELIA_LIVE_TOOL_OK"
    prompt = {"model": "velia-pro", "stream": False, "max_completion_tokens": 4096,
        "messages": [{"role": "system", "content": "You are VELIA. Call read_probe once. After its result, return exactly its text."},
                     {"role": "user", "content": "Use read_probe to read the verification value."}],
        "tools": [{"type": "function", "function": {"name": "read_probe", "description": "Read a fixed verification value.",
                  "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}],
        "tool_choice": "required"}
    async with ClientSession(timeout=ClientTimeout(total=180, sock_read=90), cookie_jar=DummyCookieJar()) as client:
        async with client.get(config.auth_origin + "/mobile-api/v1/health", allow_redirects=False) as response:
            health = await response.json()
            if response.status != 200 or health.get("enabled") is not True:
                raise RuntimeError("identity_health_failed")
        async with client.get(config.auth_origin + "/mobile-api/v1/me", allow_redirects=False) as response:
            if response.status != 401:
                raise RuntimeError("identity_unauthorized_gate_failed")
        async with client.post(base + "/chat/completions", json=validate_payload(prompt), headers=headers,
                               allow_redirects=False) as response:
            if response.status != 200:
                raise RuntimeError("provider_tool_status_" + str(response.status))
            result = await response.json()
        message = result["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        if len(calls) != 1 or calls[0]["function"]["name"] != "read_probe" or not calls[0].get("id"):
            raise RuntimeError("provider_tool_call_missing")
        # Preserve provider reasoning fields required by thinking tool models.
        prompt["messages"].extend([message, {"role": "tool", "tool_call_id": calls[0]["id"], "content": marker}])
        prompt.update(stream=True, tool_choice="none", max_completion_tokens=1024)
        text, done = "", False
        async with client.post(base + "/chat/completions", json=validate_payload(prompt), headers=headers,
                               allow_redirects=False) as response:
            if response.status != 200 or "text/event-stream" not in response.headers.get("Content-Type", ""):
                raise RuntimeError("provider_stream_status_" + str(response.status))
            while True:
                line = await response.content.readline()
                if not line:
                    break
                if not line.startswith(b"data: "):
                    continue
                data = line[6:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                chunk = json.loads(data)
                for choice in chunk.get("choices", []):
                    text += choice.get("delta", {}).get("content") or ""
                if len(text) > 16384:
                    raise RuntimeError("provider_probe_output_too_large")
        if not done or text.strip() != marker:
            raise RuntimeError("provider_stream_verification_failed")
    receipt = {"ok": True, "commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "identity_health": True, "identity_rejects_no_token": True,
            "live_tool_call": True, "correlated_tool_result": True,
            "live_sse": True, "model_calls": 2, "owner_pairing_verified": False}
    if os.getenv("VELIA_DESKTOP_FLASH_ENABLED", "").lower() in {"true", "1"}:
        receipt.update(await run_flash_probe())
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", required=True, action="store_true")
    parser.parse_args()
    print("VELIA_DESKTOP_LIVE_PROBE " + json.dumps(asyncio.run(run_probe()), sort_keys=True), flush=True)
