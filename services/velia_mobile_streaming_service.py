import asyncio
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from services.velia_chat_streaming_runtime_patch import run_streaming_send


logger = logging.getLogger(__name__)

_STREAM_ROUTE = "/mobile-api/v1/conversations/{conversation_id}/messages/stream"
_DELTA_BATCH_CHARS = 48
_DELTA_BATCH_SECONDS = 0.04
_KEEPALIVE_SECONDS = 8.0


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _sse_event(event_type: str, **fields: Any) -> bytes:
    payload = {"type": str(event_type), **fields}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {encoded}\n\n".encode("utf-8")


def _stream_error_code(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("error") or result.get("reason") or "generation_failed")
    return "generation_failed"


async def _write_if_connected(
    response: web.StreamResponse,
    connected: threading.Event,
    payload: bytes,
) -> bool:
    if not connected.is_set():
        return False
    try:
        await response.write(payload)
        return True
    except (ConnectionResetError, RuntimeError, BrokenPipeError):
        connected.clear()
        return False


def setup_velia_mobile_streaming_route(
    app: web.Application,
    chat_module: Any,
    routes_module: Any,
) -> None:
    if app.get("velia_mobile_streaming_route_installed"):
        return

    async def handle_stream(request: web.Request) -> web.StreamResponse:
        if not routes_module._mobile_api_available():
            return routes_module._disabled_response()
        if not _env_bool("VELIA_CHAT_STREAMING_ENABLED", True):
            return routes_module._json_response(
                {"ok": False, "error": "streaming_disabled"},
                status=503,
            )

        auth = routes_module._require_mobile_auth(request)
        if not auth:
            return routes_module._json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        data = await routes_module._read_json(request)
        if data is None:
            return routes_module._json_response(
                {"ok": False, "error": "invalid_json"},
                status=400,
            )

        content = str(data.get("content") or "")
        idempotency_key = str(
            request.headers.get("Idempotency-Key")
            or data.get("idempotency_key")
            or ""
        ).strip()
        conversation_id = str(request.match_info["conversation_id"])
        user_id = int(auth["user_id"])

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-store, no-transform",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Tuple[str, str]] = asyncio.Queue()
        connected = threading.Event()
        connected.set()

        def enqueue(event_type: str, text: str = "") -> None:
            if not connected.is_set():
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (event_type, text))
            except RuntimeError:
                connected.clear()

        worker = asyncio.create_task(
            asyncio.to_thread(
                run_streaming_send,
                chat_module.send_message,
                user_id=user_id,
                conversation_id=conversation_id,
                content=content,
                idempotency_key=idempotency_key,
                on_delta=lambda delta: enqueue("delta", delta),
                on_reset=lambda: enqueue("reset"),
            )
        )

        pending_delta: list[str] = []
        pending_chars = 0
        last_flush = time.monotonic()
        last_write = last_flush

        async def flush_delta() -> None:
            nonlocal pending_chars, last_flush, last_write
            if not pending_delta:
                return
            combined = "".join(pending_delta)
            pending_delta.clear()
            pending_chars = 0
            if await _write_if_connected(
                response,
                connected,
                _sse_event("delta", text=combined),
            ):
                last_write = time.monotonic()
            last_flush = time.monotonic()

        try:
            if await _write_if_connected(
                response,
                connected,
                b"retry: 1000\n\n" + _sse_event("ready"),
            ):
                last_write = time.monotonic()

            while True:
                if worker.done() and queue.empty():
                    break
                event: Optional[Tuple[str, str]] = None
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    pass

                now = time.monotonic()
                if event is not None:
                    event_type, event_text = event
                    if event_type == "delta" and event_text:
                        pending_delta.append(event_text)
                        pending_chars += len(event_text)
                    elif event_type == "reset":
                        await flush_delta()
                        if await _write_if_connected(
                            response,
                            connected,
                            _sse_event("reset"),
                        ):
                            last_write = time.monotonic()

                if pending_delta and (
                    pending_chars >= _DELTA_BATCH_CHARS
                    or now - last_flush >= _DELTA_BATCH_SECONDS
                    or worker.done()
                ):
                    await flush_delta()

                if connected.is_set() and now - last_write >= _KEEPALIVE_SECONDS:
                    if await _write_if_connected(response, connected, b": ping\n\n"):
                        last_write = time.monotonic()

            result = await worker
            await flush_delta()
            if isinstance(result, dict) and result.get("ok"):
                await _write_if_connected(
                    response,
                    connected,
                    _sse_event("complete", result=result),
                )
            else:
                await _write_if_connected(
                    response,
                    connected,
                    _sse_event(
                        "error",
                        error=_stream_error_code(result),
                        result=result if isinstance(result, dict) else None,
                    ),
                )
        except asyncio.CancelledError:
            connected.clear()
            logger.info(
                "VELIA_STREAM_CLIENT_CANCELLED user_id=%s conversation_id=%s",
                user_id,
                conversation_id,
            )
            raise
        except Exception as exc:
            connected.clear()
            logger.warning(
                "VELIA_STREAM_CLIENT_DISCONNECTED user_id=%s conversation_id=%s error=%s",
                user_id,
                conversation_id,
                exc.__class__.__name__,
            )
        finally:
            connected.clear()
            try:
                await response.write_eof()
            except Exception:
                pass

        return response

    app.router.add_post(_STREAM_ROUTE, handle_stream)
    app["velia_mobile_streaming_route_installed"] = True
    logger.info("VELIA_MOBILE_STREAMING_ROUTE_INSTALLED")
