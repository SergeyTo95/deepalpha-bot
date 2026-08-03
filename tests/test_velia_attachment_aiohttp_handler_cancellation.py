from types import SimpleNamespace

from services import aiohttp_handler_cancellation_service as cancellation


class _Task:
    def __init__(self, done=False):
        self._done = done
        self.cancel_calls = 0

    def done(self):
        return self._done

    def cancel(self):
        self.cancel_calls += 1


def test_legacy_connection_lost_cancels_active_handler_once():
    original_calls = []

    class Handler:
        def __init__(self):
            self._task_handler = _Task(done=False)

        def connection_lost(self, exc):
            original_calls.append(exc)
            return "original-result"

    assert cancellation._patch_request_handler_class(Handler) is True
    assert cancellation._patch_request_handler_class(Handler) is False

    handler = Handler()
    error = ConnectionResetError("gone")
    result = handler.connection_lost(error)

    assert result == "original-result"
    assert handler._task_handler.cancel_calls == 1
    assert original_calls == [error]


def test_legacy_connection_lost_does_not_cancel_completed_task():
    class Handler:
        def __init__(self):
            self._task_handler = _Task(done=True)

        def connection_lost(self, exc):
            return None

    cancellation._patch_request_handler_class(Handler)
    handler = Handler()
    handler.connection_lost(None)

    assert handler._task_handler.cancel_calls == 0


def test_legacy_run_app_installs_backport_without_unsupported_keyword(monkeypatch):
    installed = []

    def legacy_run_app(app, *, host=None, port=None):
        return (app, host, port)

    web_module = SimpleNamespace(run_app=legacy_run_app)
    monkeypatch.setattr(
        cancellation,
        "_install_legacy_handler_cancellation",
        lambda: installed.append(True) or True,
    )

    kwargs = cancellation.handler_cancellation_run_app_kwargs(web_module)

    assert kwargs == {}
    assert installed == [True]
    assert web_module.run_app("app", host="0.0.0.0", port=3000) == (
        "app",
        "0.0.0.0",
        3000,
    )


def test_native_run_app_uses_supported_handler_cancellation_keyword(monkeypatch):
    installed = []

    def native_run_app(app, *, host=None, port=None, handler_cancellation=False):
        return (app, host, port, handler_cancellation)

    web_module = SimpleNamespace(run_app=native_run_app)
    monkeypatch.setattr(
        cancellation,
        "_install_legacy_handler_cancellation",
        lambda: installed.append(True) or True,
    )

    kwargs = cancellation.handler_cancellation_run_app_kwargs(web_module)

    assert kwargs == {"handler_cancellation": True}
    assert installed == []
    assert web_module.run_app("app", host="0.0.0.0", port=3000, **kwargs) == (
        "app",
        "0.0.0.0",
        3000,
        True,
    )
