import asyncio
import json
from types import SimpleNamespace

from services import kimi_gateway
from services import velia_attachment_service
import velia_mobile_attachment_routes as routes


class _WrongCharsetResponse:
    status_code = 200
    headers = {}

    def __init__(self):
        self.content = json.dumps(
            {
                "choices": [
                    {
                        "message": {"content": "На фото кот"},
                        "finish_reason": "stop",
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def json(self):
        return json.loads(self.content.decode("latin-1"))


def test_kimi_json_decoder_ignores_wrong_provider_charset():
    data = kimi_gateway._decode_json_response(_WrongCharsetResponse())
    assert kimi_gateway._extract_final_text(data) == "На фото кот"


def test_kimi_text_repair_recovers_common_utf8_mojibake():
    broken = "На фото кот".encode("utf-8").decode("latin-1")
    assert kimi_gateway._repair_utf8_mojibake(broken) == "На фото кот"
    assert kimi_gateway._repair_utf8_mojibake("Обычный русский текст") == (
        "Обычный русский текст"
    )


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.executed = []
        self.closed = False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


def test_attachment_content_is_owner_scoped_and_returns_original_image(monkeypatch):
    connection = _Connection(
        (
            "attachment-1",
            "image/png",
            "image",
            7,
            memoryview(b"pngdata"),
            120,
            80,
        )
    )
    monkeypatch.setattr(velia_attachment_service, "get_connection", lambda: connection)

    result = velia_attachment_service.get_attachment_content(42, "attachment-1")

    assert result == {
        "id": "attachment-1",
        "mime_type": "image/png",
        "kind": "image",
        "byte_size": 7,
        "content_bytes": b"pngdata",
        "width": 120,
        "height": 80,
    }
    sql, params = connection.cursor_value.executed[0]
    assert "user_id=%s" in sql
    assert "kind='image'" in sql
    assert params == ("attachment-1", 42)
    assert connection.cursor_value.closed is True
    assert connection.closed is True


class _Router:
    def __init__(self):
        self.get = {}
        self.post = {}
        self.delete = {}

    def add_get(self, path, handler):
        self.get[path] = handler

    def add_post(self, path, handler):
        self.post[path] = handler

    def add_delete(self, path, handler):
        self.delete[path] = handler


class _App:
    def __init__(self):
        self.router = _Router()


def test_authenticated_content_route_returns_no_store_image_bytes(monkeypatch):
    app = _App()
    monkeypatch.setattr(routes, "_scrub_legacy_payloads_best_effort", lambda: None)
    monkeypatch.setattr(routes, "_attachment_api_unavailable_error", lambda: "")
    monkeypatch.setattr(routes, "_require_mobile_auth", lambda _request: {"user_id": 42})
    monkeypatch.setattr(
        routes,
        "get_attachment_content",
        lambda user_id, attachment_id: {
            "id": attachment_id,
            "mime_type": "image/png",
            "kind": "image",
            "byte_size": 7,
            "content_bytes": b"pngdata",
            "width": 120,
            "height": 80,
        }
        if user_id == 42
        else None,
    )
    routes.setup_velia_mobile_attachment_routes(app)
    handler = app.router.get[
        "/mobile-api/v1/attachments/{attachment_id}/content"
    ]
    request = SimpleNamespace(match_info={"attachment_id": "attachment-1"})

    response = asyncio.run(handler(request))

    assert response.status == 200
    assert response.body == b"pngdata"
    assert response.content_type == "image/png"
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
