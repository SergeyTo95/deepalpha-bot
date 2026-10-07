"""Private persistent VELIA Agent Core Browser service.

The public Railway surface exposes health only. Browser tasks require a separate
internal bearer key and run through VELIA Flash with no paid-model fallback.
Each authenticated VELIA account receives one isolated long-lived Chromium and
one persisted headless Agent session until its idle TTL expires.
"""
import asyncio
from agent_core.browser import storage
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import time

from aiohttp import ClientSession, ClientTimeout, web

from velia_desktop_routes import setup_velia_desktop_routes
from agent_core.browser.state_store import (
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

MAX_RUN_BODY = 16 * 1024
MAX_PROMPT_CHARS = 8000
RUN_TIMEOUT_SECONDS = 450
RUN_CONCURRENCY = 1
DEFAULT_SESSION_IDLE_SECONDS = 3600
DEFAULT_PROFILE_RETENTION_SECONDS = 30 * 24 * 3600
DEFAULT_MAX_SESSIONS = 4
DEFAULT_TAKEOVER_IDLE_SECONDS = 600

fixture_id = "velia-agent-core-browser-service"
fixture_token = secrets.token_hex(32)
run_sem = asyncio.Semaphore(RUN_CONCURRENCY)


def _bounded_int(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


SESSION_IDLE_SECONDS = _bounded_int(
    "VELIA_AGENT_CORE_SESSION_IDLE_SECONDS", DEFAULT_SESSION_IDLE_SECONDS, 300, 86400
)
MAX_SESSIONS = _bounded_int(
    "VELIA_AGENT_CORE_MAX_SESSIONS", DEFAULT_MAX_SESSIONS, 1, 16
)
PROFILE_RETENTION_SECONDS = _bounded_int(
    "VELIA_AGENT_CORE_PROFILE_RETENTION_SECONDS",
    DEFAULT_PROFILE_RETENTION_SECONDS,
    3600,
    180 * 24 * 3600,
)
TAKEOVER_IDLE_SECONDS = _bounded_int(
    "VELIA_AGENT_CORE_TAKEOVER_IDLE_SECONDS",
    DEFAULT_TAKEOVER_IDLE_SECONDS,
    60,
    1800,
)
SESSION_BASE = Path(
    os.getenv("VELIA_AGENT_CORE_SESSION_ROOT", "/tmp/velia-agent-core-sessions")
).resolve()
if not SESSION_BASE.is_absolute():
    raise RuntimeError("browser_session_root_must_be_absolute")


@dataclass
class BrowserSession:
    user_key: str
    root: Path
    endpoint: str
    browser: asyncio.subprocess.Process
    agent_session_id: str | None = None
    agent_context_turns: int = 0
    takeover_kind: str | None = None
    takeover_expires_at: float = 0.0
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


sessions: dict[str, BrowserSession] = {}
sessions_lock = asyncio.Lock()

storage_reused_at_boot = False
storage_report = {}
storage_lock = asyncio.Lock()


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
    return hmac.compare_digest(header[7:], expected)


def _request_identity(request):
    user_id = request.headers.get("X-Velia-User", "").strip()
    conversation = request.headers.get("X-Velia-Session", "").strip() or "default"
    if not re.fullmatch(r"[1-9][0-9]{0,18}", user_id):
        return None
    if conversation != "default" and not re.fullmatch(
        r"[A-Za-z0-9_-]{8,128}", conversation
    ):
        return None
    return user_id, conversation


def _session_key(user_id, conversation="default"):
    material = f"velia-browser:{user_id}:{conversation}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def _session_root(user_id, conversation="default"):
    return SESSION_BASE / _session_key(user_id, conversation)


def _agent_session_path(root):
    return root / "agent-session-id"


def _takeover_state_path(root):
    return root / "takeover-state.json"


async def _read_takeover_state(root):
    try:
        payload = json.loads(await asyncio.to_thread(
            _takeover_state_path(root).read_text, encoding="utf-8"
        ))
    except (OSError, ValueError):
        return None, 0.0
    kind = payload.get("kind") if isinstance(payload, dict) else None
    expires_at = payload.get("expires_at") if isinstance(payload, dict) else None
    if (
        kind in {"credentials", "otp", "passkey", "captcha", "device_approval"}
        and type(expires_at) in {int, float}
        and float(expires_at) > time.time()
    ):
        return kind, float(expires_at)
    try:
        await asyncio.to_thread(_takeover_state_path(root).unlink, missing_ok=True)
    except OSError:
        pass
    return None, 0.0


async def _write_takeover_state(root, kind, expires_at):
    target = _takeover_state_path(root)
    temporary = root / f".takeover-state.{secrets.token_hex(4)}.tmp"
    payload = json.dumps(
        {"kind": kind, "expires_at": float(expires_at)},
        separators=(",", ":"),
    )
    await asyncio.to_thread(temporary.write_text, payload, encoding="utf-8")
    await asyncio.to_thread(os.chmod, temporary, 0o600)
    await asyncio.to_thread(os.replace, temporary, target)


async def _clear_takeover_state(root):
    try:
        await asyncio.to_thread(_takeover_state_path(root).unlink, missing_ok=True)
    except OSError:
        pass


async def _read_agent_session_id(root):
    try:
        value = (await asyncio.to_thread(
            _agent_session_path(root).read_text, encoding="utf-8"
        )).strip()
    except OSError:
        return None
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", value):
        return value
    return None


async def _write_agent_session_id(root, session_id):
    target = _agent_session_path(root)
    temporary = root / f".agent-session-id.{secrets.token_hex(4)}.tmp"
    await asyncio.to_thread(temporary.write_text, session_id + "\n", encoding="utf-8")
    await asyncio.to_thread(os.chmod, temporary, 0o600)
    await asyncio.to_thread(os.replace, temporary, target)


async def _touch_profile(root):
    marker = root / ".last-used"
    await asyncio.to_thread(marker.write_text, str(int(time.time())) + "\n", encoding="utf-8")
    await asyncio.to_thread(os.chmod, marker, 0o600)


async def _ensure_storage_sentinel():
    await asyncio.to_thread(SESSION_BASE.mkdir, parents=True, exist_ok=True, mode=0o700)
    target = SESSION_BASE / ".storage-sentinel"
    try:
        value = (await asyncio.to_thread(target.read_text, encoding="utf-8")).strip()
    except OSError:
        value = ""
    if re.fullmatch(r"[0-9a-f]{32}", value):
        return True
    value = secrets.token_hex(16)
    temporary = SESSION_BASE / f".storage-sentinel.{secrets.token_hex(4)}.tmp"
    await asyncio.to_thread(temporary.write_text, value + "\n", encoding="utf-8")
    await asyncio.to_thread(os.chmod, temporary, 0o600)
    try:
        await asyncio.to_thread(os.replace, temporary, target)
    finally:
        try:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
        except OSError:
            pass
    return False


async def _warm_flash():
    if not FLASH_BASE_URL or not FLASH_API_KEY:
        raise RuntimeError("browser_flash_configuration_missing")
    timeout = ClientTimeout(total=5)
    async with ClientSession(timeout=timeout) as client:
        for _ in range(60):
            try:
                async with client.get(
                    FLASH_BASE_URL.rstrip("/") + "/health",
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        return
            except (OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(2)
    raise RuntimeError("browser_flash_startup_timeout")


async def _stop_process(process):
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _snapshot_session_cookies(session):
    if session.browser.returncode is not None:
        return 0
    try:
        count = await save_session_cookies(session.endpoint, session.root)
        print(
            "VELIA_AGENT_CORE_COOKIE_SNAPSHOT "
            + json.dumps({"count": count}, sort_keys=True),
            flush=True,
        )
        return count
    except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
        print(
            f"VELIA_AGENT_CORE_COOKIE_SNAPSHOT_FAILED detail={str(exc)[:160]!r}",
            flush=True,
        )
        return 0


async def _dispose_session(session, purge=False):
    await _snapshot_session_cookies(session)
    await _stop_process(session.browser)
    if purge:
        await asyncio.to_thread(shutil.rmtree, session.root, True)
    else:
        await _touch_profile(session.root)


async def _cleanup_expired_sessions():
    now = time.monotonic()
    expired = []
    async with storage_lock:
        async with sessions_lock:
            for key, session in list(sessions.items()):
                if session.lock.locked():
                    continue
                if now - session.last_used >= SESSION_IDLE_SECONDS:
                    expired.append(sessions.pop(key))
        for session in expired:
            await _dispose_session(session)


async def _cleanup_retained_profiles():
    cutoff = time.time() - PROFILE_RETENTION_SECONDS
    async with sessions_lock:
        active = set(sessions)
    try:
        children = await asyncio.to_thread(lambda: list(SESSION_BASE.iterdir()))
    except OSError:
        return
    for root in children:
        if not root.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", root.name):
            continue
        if root.name in active:
            continue
        marker = root / ".last-used"
        try:
            stat = await asyncio.to_thread(marker.stat if marker.exists() else root.stat)
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            await asyncio.to_thread(shutil.rmtree, root, True)


async def _maintain_storage():
    global storage_report
    async with storage_lock:
        # Session creation and maintenance share this lock so caches are never
        # removed from a Chromium being started or serving a takeover.
        async with sessions_lock:
            storage_report = await asyncio.to_thread(storage.maintain, SESSION_BASE, set(sessions))
        if storage_report['cache_bytes_removed'] or storage_report['low_space']:
            print('VELIA_AGENT_CORE_STORAGE_MAINTENANCE ' + json.dumps(storage_report, sort_keys=True), flush=True)
    return storage_report


async def _storage_loop():
    while True:
        await asyncio.sleep(300)
        try:
            await _cleanup_expired_sessions()
            await _cleanup_retained_profiles()
            await _maintain_storage()
        except OSError as exc:
            print('VELIA_AGENT_CORE_STORAGE_MAINTENANCE_FAILED errno=' + str(exc.errno), flush=True)


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


async def _new_session(user_id, conversation="default"):
    root = _session_root(user_id, conversation)
    profile = root / "chromium"
    workspace = root / "workspace"
    dsh_home = root / "dsh-home"
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True, mode=0o700)
    await asyncio.to_thread(profile.mkdir, parents=True, exist_ok=True, mode=0o700)
    await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True, mode=0o700)
    await asyncio.to_thread(dsh_home.mkdir, parents=True, exist_ok=True, mode=0o700)
    for stale_name in ("DevToolsActivePort", "SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            await asyncio.to_thread((profile / stale_name).unlink, missing_ok=True)
        except OSError:
            pass

    agent_session_id = await _read_agent_session_id(root)
    takeover_kind, takeover_expires_at = await _read_takeover_state(root)
    await _touch_profile(root)
    chromium = os.getenv("VELIA_AGENT_CORE_CHROMIUM", "/usr/bin/chromium")
    process = await asyncio.create_subprocess_exec(
        chromium,
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",
        "--disk-cache-size=16777216",
        "--media-cache-size=8388608",
        "--disable-component-update",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        port = await _wait_for_debug_port(profile, process)
        endpoint = f"http://127.0.0.1:{port}"
        restored_cookies = await restore_session_cookies(endpoint, root)
        if restored_cookies:
            print(
                "VELIA_AGENT_CORE_COOKIE_RESTORED "
                + json.dumps({"count": restored_cookies}, sort_keys=True),
                flush=True,
            )
    except Exception:
        await _stop_process(process)
        raise
    return BrowserSession(
        user_key=_session_key(user_id, conversation),
        root=root,
        endpoint=endpoint,
        browser=process,
        agent_session_id=agent_session_id,
        takeover_kind=takeover_kind,
        takeover_expires_at=takeover_expires_at,
    )


async def _get_session(user_id, conversation="default"):
    await _cleanup_expired_sessions()
    await _cleanup_retained_profiles()
    await _maintain_storage()
    await asyncio.to_thread(storage.ensure_capacity, SESSION_BASE, _session_root(user_id, conversation))
    key = _session_key(user_id, conversation)
    stale = None
    async with sessions_lock:
        current = sessions.get(key)
        if current and current.browser.returncode is None:
            current.last_used = time.monotonic()
            return current, True
        if current:
            stale = sessions.pop(key)
        if len(sessions) >= MAX_SESSIONS:
            raise RuntimeError("browser_session_capacity")
    if stale:
        await _dispose_session(stale)

    async with storage_lock:
        created = await _new_session(user_id, conversation)
        async with sessions_lock:
            race = sessions.get(key)
            if race and race.browser.returncode is None:
                winner = race
            else:
                sessions[key] = created
                winner = created
    if winner is not created:
        await _dispose_session(created)
        winner.last_used = time.monotonic()
        return winner, True
    return created, False


async def health(_request):
    await _cleanup_expired_sessions()
    return web.json_response({
        "ok": True,
        "service": "velia-agent-core-browser",
        "model": "velia-flash",
        "browser": "playwright-mcp-attach",
        "persistent_sessions": True,
        "active_sessions": len(sessions),
        "session_idle_seconds": SESSION_IDLE_SECONDS,
        "profile_retention_seconds": PROFILE_RETENTION_SECONDS,
        "durable_storage": not str(SESSION_BASE).startswith("/tmp/"),
        "storage_reused_at_boot": storage_reused_at_boot,
        "storage": {k: storage_report.get(k) for k in ("free_bytes", "total_bytes", "low_space")},
        "session_cookie_snapshot": True,
        "manual_takeover": True,
        "takeover_idle_seconds": TAKEOVER_IDLE_SECONDS,
        "public_agent": False,
        "run_api_configured": bool(os.getenv("VELIA_AGENT_CORE_INTERNAL_KEY", "").strip()),
        "revision": os.getenv("RAILWAY_GIT_COMMIT_SHA", ""),
    })


async def run_browser_task(request):
    if not _internal_authorized(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    identity = _request_identity(request)
    if not identity:
        return web.json_response({"ok": False, "error": "invalid_session_identity"}, status=400)
    user_id, conversation = identity
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
    if (
        not isinstance(payload, dict)
        or set(payload) != {"prompt"}
        or not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt) > MAX_PROMPT_CHARS
    ):
        return web.json_response({"ok": False, "error": "invalid_prompt"}, status=400)

    if run_sem.locked():
        return web.json_response({"ok": False, "error": "browser_agent_busy"}, status=429)

    child = None
    async with run_sem:
        try:
            await _warm_flash()
            session, reused = await _get_session(user_id, conversation)
            async with session.lock:
                if session.takeover_kind and session.takeover_expires_at <= time.time():
                    session.takeover_kind = None
                    session.takeover_expires_at = 0.0
                    await _clear_takeover_state(session.root)
                if session.takeover_kind:
                    return web.json_response(
                        {"ok": False, "error": "browser_takeover_required"}, status=409
                    )
                runtime = os.getenv(
                    "VELIA_AGENT_CORE_RUNTIME", "/opt/velia-agent-core/runtime"
                )
                script = Path(__file__).with_name("run_task.mjs")
                port = int(os.getenv("PORT", "8080"))
                if session.agent_context_turns >= 2:
                    session.agent_session_id = None
                    session.agent_context_turns = 0
                    print(
                        "VELIA_AGENT_CORE_CONTEXT_ROLLOVER "
                        + json.dumps({"reason": "flash_8k_budget"}, sort_keys=True),
                        flush=True,
                    )
                child = await asyncio.create_subprocess_exec(
                    "node",
                    str(script),
                    f"http://127.0.0.1:{port}",
                    fixture_token,
                    runtime,
                    session.endpoint,
                    str(session.root),
                    session.agent_session_id or "-",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
                )
                stdout, stderr = await asyncio.wait_for(
                    child.communicate(prompt.strip().encode("utf-8")),
                    timeout=RUN_TIMEOUT_SECONDS,
                )
                if child.returncode != 0:
                    message = stderr.decode(errors="replace")[-2000:]
                    print(
                        "VELIA_AGENT_CORE_BROWSER_RUN_FAILED returncode="
                        f"{child.returncode} detail={message!r}",
                        flush=True,
                    )
                    return web.json_response(
                        {"ok": False, "error": "browser_agent_failed"}, status=502
                    )
                try:
                    result = json.loads(stdout)
                except (ValueError, UnicodeDecodeError):
                    return web.json_response(
                        {"ok": False, "error": "browser_agent_invalid_result"},
                        status=502,
                    )
                session_id = result.get("session_id")
                if (
                    result.get("ok") is not True
                    or result.get("model") != "velia-flash"
                    or not isinstance(session_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", session_id)
                ):
                    return web.json_response(
                        {"ok": False, "error": "browser_agent_invalid_result"},
                        status=502,
                    )
                session.agent_session_id = session_id
                session.agent_context_turns += 1
                await _write_agent_session_id(session.root, session_id)
                handoff_kind = result.get("user_action_required")
                if handoff_kind in {"credentials", "otp", "passkey", "captcha", "device_approval"}:
                    session.takeover_kind = handoff_kind
                    session.takeover_expires_at = time.time() + TAKEOVER_IDLE_SECONDS
                    await _write_takeover_state(
                        session.root, session.takeover_kind, session.takeover_expires_at
                    )
                else:
                    session.takeover_kind = None
                    session.takeover_expires_at = 0.0
                    await _clear_takeover_state(session.root)
                await _touch_profile(session.root)
                await _snapshot_session_cookies(session)
                session.last_used = time.monotonic()
                result["persistent"] = True
                result["session_reused"] = reused
                print(
                    "VELIA_AGENT_CORE_BROWSER_RUN_OK "
                    + json.dumps(
                        {
                            "session_reused": reused,
                            "active_sessions": len(sessions),
                            "tool_count": result.get("tool_count", 0),
                            "distinct_tools": result.get("distinct_tools", 0),
                            "answer_chars": len(result.get("text") or ""),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                return web.json_response(
                    result, headers={"Cache-Control": "no-store"}
                )
        except asyncio.TimeoutError:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            return web.json_response(
                {"ok": False, "error": "browser_agent_timeout"}, status=504
            )
        except (OSError, RuntimeError) as exc:
            print(
                f"VELIA_AGENT_CORE_BROWSER_RUN_ERROR code={str(exc)[:120]!r}",
                flush=True,
            )
            return web.json_response(
                {"ok": False, "error": str(exc) if str(exc) in {"browser_storage_capacity", "browser_profile_capacity"} else "browser_agent_unavailable"}, status=503
            )
        except asyncio.CancelledError:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            raise


async def _takeover_session(request):
    if not _internal_authorized(request):
        return None, web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    identity = _request_identity(request)
    if not identity:
        return None, web.json_response({"ok": False, "error": "invalid_session_identity"}, status=400)
    user_id, conversation = identity
    try:
        session, _reused = await _get_session(user_id, conversation)
    except (OSError, RuntimeError):
        return None, web.json_response(
            {"ok": False, "error": "browser_takeover_unavailable"}, status=503
        )
    if session.takeover_kind and session.takeover_expires_at <= time.time():
        session.takeover_kind = None
        session.takeover_expires_at = 0.0
        await _clear_takeover_state(session.root)
    if not session.takeover_kind:
        return None, web.json_response(
            {"ok": False, "error": "browser_takeover_not_required"}, status=409
        )
    return session, None


async def browser_takeover_state(request):
    session, failure = await _takeover_session(request)
    if failure is not None:
        return failure
    async with session.lock:
        try:
            state = await capture_takeover_state(session.endpoint)
        except (OSError, RuntimeError, asyncio.TimeoutError):
            return web.json_response(
                {"ok": False, "error": "browser_takeover_unavailable"}, status=503
            )
        session.last_used = time.monotonic()
        return web.json_response({
            "ok": True,
            "kind": session.takeover_kind,
            "expires_in": max(0, min(TAKEOVER_IDLE_SECONDS, int(session.takeover_expires_at - time.time()))),
            "image": state["image"],
            "viewport": {"width": state["width"], "height": state["height"]},
            "url": state["url"],
            "title": state["title"],
        }, headers={"Cache-Control": "no-store"})


async def browser_takeover_action(request):
    session, failure = await _takeover_session(request)
    if failure is not None:
        return failure
    try:
        payload = await request.json()
    except web.HTTPRequestEntityTooLarge:
        return web.json_response({"ok": False, "error": "request_too_large"}, status=413)
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "invalid_takeover_action"}, status=400)
    action = payload.get("action")
    valid_fields = {
        "click": {"action", "x", "y"},
        "text": {"action", "text"},
        "key": {"action", "key"},
        "scroll": {"action", "delta_y"},
        "finish": {"action"},
    }
    if action not in valid_fields or set(payload) != valid_fields[action]:
        return web.json_response({"ok": False, "error": "invalid_takeover_action"}, status=400)

    async with session.lock:
        try:
            if action == "click":
                x, y = payload.get("x"), payload.get("y")
                if (
                    isinstance(x, bool) or isinstance(y, bool)
                    or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
                ):
                    raise ValueError
                await takeover_click(session.endpoint, x, y)
            elif action == "text":
                text = payload.get("text")
                if not isinstance(text, str) or not 1 <= len(text) <= 4096:
                    raise ValueError
                await takeover_insert_text(session.endpoint, text)
            elif action == "key":
                await takeover_press_key(session.endpoint, payload.get("key"))
            elif action == "scroll":
                delta = payload.get("delta_y")
                if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                    raise ValueError
                await takeover_scroll(session.endpoint, delta)
            elif action == "finish":
                session.takeover_kind = None
                session.takeover_expires_at = 0.0
                await _clear_takeover_state(session.root)
                await _snapshot_session_cookies(session)
                await _touch_profile(session.root)
                session.last_used = time.monotonic()
                return web.json_response(
                    {"ok": True, "finished": True},
                    headers={"Cache-Control": "no-store"},
                )
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid_takeover_action"}, status=400)
        except (OSError, RuntimeError, asyncio.TimeoutError):
            return web.json_response(
                {"ok": False, "error": "browser_takeover_unavailable"}, status=503
            )

        session.takeover_expires_at = time.time() + TAKEOVER_IDLE_SECONDS
        await _write_takeover_state(
            session.root, session.takeover_kind, session.takeover_expires_at
        )
        session.last_used = time.monotonic()
        state = await capture_takeover_state(session.endpoint)
        return web.json_response({
            "ok": True,
            "kind": session.takeover_kind,
            "expires_in": TAKEOVER_IDLE_SECONDS,
            "image": state["image"],
            "viewport": {"width": state["width"], "height": state["height"]},
            "url": state["url"],
            "title": state["title"],
        }, headers={"Cache-Control": "no-store"})


async def session_lifecycle(_app):
    global storage_reused_at_boot
    await asyncio.to_thread(SESSION_BASE.mkdir, parents=True, exist_ok=True, mode=0o700)
    await _maintain_storage()
    storage_reused_at_boot = await _ensure_storage_sentinel()
    print(
        "VELIA_AGENT_CORE_STORAGE_READY "
        + json.dumps({
            "durable_storage": not str(SESSION_BASE).startswith("/tmp/"),
            "reused": storage_reused_at_boot,
            "storage": storage_report,
        }, sort_keys=True),
        flush=True,
    )
    maintenance_task = asyncio.create_task(_storage_loop())
    try:
        yield
    finally:
        maintenance_task.cancel()
        await asyncio.gather(maintenance_task, return_exceptions=True)
    async with sessions_lock:
        remaining = list(sessions.values())
        sessions.clear()
    for session in remaining:
        await _dispose_session(session)
    await _cleanup_retained_profiles()


app = web.Application(client_max_size=MAX_RUN_BODY)
app.cleanup_ctx.append(session_lifecycle)
app.router.add_get("/health", health)
app.router.add_post("/v1/run", run_browser_task)
app.router.add_get("/v1/takeover/state", browser_takeover_state)
app.router.add_post("/v1/takeover/action", browser_takeover_action)
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
