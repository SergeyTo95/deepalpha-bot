"""Explicit operator-only live check; this is not a public authentication bypass.

Two bounded model calls check tool correlation and SSE. The tool supplies a
fixed test value, not user data. This does not claim owner-account pairing.
"""
import argparse
import asyncio
import json
import os
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar
from desktop.gateway import GatewayConfig
from velia_desktop_routes import validate_payload


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
    return {"ok": True, "commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
            "identity_health": True, "identity_rejects_no_token": True,
            "live_tool_call": True, "correlated_tool_result": True,
            "live_sse": True, "model_calls": 2, "owner_pairing_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", required=True, action="store_true")
    parser.parse_args()
    print("VELIA_DESKTOP_LIVE_PROBE " + json.dumps(asyncio.run(run_probe()), sort_keys=True), flush=True)
