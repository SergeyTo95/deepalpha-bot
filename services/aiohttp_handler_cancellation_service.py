import inspect
from typing import Any, Dict, Type


_PATCH_MARKER = "_velia_handler_cancellation_installed"


def _patch_request_handler_class(handler_class: Type[Any]) -> bool:
    """Backport aiohttp 3.9 handler cancellation to the 3.8 protocol.

    aiohttp 3.8 clears ``_task_handler`` on ``connection_lost`` without
    cancelling it. Cancelling the task first makes a peer disconnect propagate
    into the active request handler, matching the opt-in 3.9 behavior.
    """
    if bool(getattr(handler_class, _PATCH_MARKER, False)):
        return False

    original_connection_lost = handler_class.connection_lost

    def connection_lost(self: Any, exc: BaseException | None) -> Any:
        task = getattr(self, "_task_handler", None)
        if task is not None and not task.done():
            task.cancel()
        return original_connection_lost(self, exc)

    handler_class.connection_lost = connection_lost
    setattr(handler_class, _PATCH_MARKER, True)
    setattr(
        handler_class,
        "_velia_original_connection_lost",
        original_connection_lost,
    )
    return True


def _install_legacy_handler_cancellation() -> bool:
    from aiohttp.web_protocol import RequestHandler

    return _patch_request_handler_class(RequestHandler)


def _supports_native_handler_cancellation(web_module: Any) -> bool:
    try:
        parameters = inspect.signature(web_module.run_app).parameters
    except (TypeError, ValueError):
        return False
    return "handler_cancellation" in parameters


def handler_cancellation_run_app_kwargs(web_module: Any) -> Dict[str, bool]:
    """Return native run_app kwargs or install the aiohttp 3.8 backport."""
    if _supports_native_handler_cancellation(web_module):
        return {"handler_cancellation": True}
    _install_legacy_handler_cancellation()
    return {}
