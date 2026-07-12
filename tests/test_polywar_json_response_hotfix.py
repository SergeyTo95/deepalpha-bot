import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import asyncio
import logging
import types



class FakeResponse:
    def __init__(self, text=None, body=None, content_type=None, headers=None, status=200):
        self.text = text
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}
        self.status = status


def load_web_api_namespace():
    source = Path("web.py").read_text()
    json_start = source.index("def _json_default")
    json_end = source.index("def _safe_int")
    handler_start = source.index("def _current_web_user")
    handler_end = source.index("async def handle_polywar_player_api")
    namespace = {
        "asyncio": asyncio,
        "date": date,
        "datetime": datetime,
        "Decimal": Decimal,
        "json": json,
        "logger": logging.getLogger("test.web"),
        "web": types.SimpleNamespace(Response=FakeResponse),
        "CORS_HEADERS": {"Access-Control-Allow-Origin": "*"},
        "get_user_by_session": lambda token: {"user_id": 99},
        "get_polywar_state": lambda user_id: {"ok": True},
    }
    exec(source[json_start:json_end] + source[handler_start:handler_end], namespace)
    return types.SimpleNamespace(**namespace)


class DummyRequest:
    cookies = {"deepalpha_session": "session-token"}


def test_json_response_serializes_datetime_date_and_decimal():
    web_api = load_web_api_namespace()
    response = web_api._json_response(
        {
            "at": datetime(2026, 7, 12, 1, 2, 3),
            "day": date(2026, 7, 12),
            "amount": Decimal("12.345"),
        }
    )

    assert response.content_type == "application/json"
    assert json.loads(response.text) == {
        "at": "2026-07-12T01:02:03",
        "day": "2026-07-12",
        "amount": "12.345",
    }


def test_polywar_state_handler_serializes_postgresql_shaped_payload(monkeypatch):
    web_api = load_web_api_namespace()
    payload = {
        "ok": True,
        "season": {
            "starts_at": datetime(2026, 7, 12, 1, 0, 0),
            "ends_at": datetime(2026, 7, 19, 1, 0, 0),
        },
        "factions": [{"id": 1, "created_at": datetime(2026, 7, 12, 2, 0, 0)}],
        "events": [{"id": 7, "created_at": datetime(2026, 7, 12, 3, 0, 0)}],
        "current_user_pending_reward": {
            "calculated_at": datetime(2026, 7, 12, 4, 0, 0),
            "balance": Decimal("42.50"),
        },
    }
    web_api._current_web_user = lambda request: {"user_id": 99}
    web_api.handle_polywar_state_api.__globals__["get_polywar_state"] = lambda user_id: payload

    response = asyncio.run(web_api.handle_polywar_state_api(DummyRequest()))

    assert response.status == 200
    assert response.content_type == "application/json"
    body = json.loads(response.text)
    assert body["season"]["starts_at"] == "2026-07-12T01:00:00"
    assert body["season"]["ends_at"] == "2026-07-19T01:00:00"
    assert body["factions"][0]["created_at"] == "2026-07-12T02:00:00"
    assert body["events"][0]["created_at"] == "2026-07-12T03:00:00"
    assert body["current_user_pending_reward"]["calculated_at"] == "2026-07-12T04:00:00"
    assert body["current_user_pending_reward"]["balance"] == "42.50"


def test_polywar_state_handler_returns_json_server_error_without_traceback(monkeypatch):
    web_api = load_web_api_namespace()
    web_api._current_web_user = lambda request: {"user_id": 99}

    def raise_runtime_error(user_id):
        raise RuntimeError("database exploded")

    web_api.handle_polywar_state_api.__globals__["get_polywar_state"] = raise_runtime_error

    response = asyncio.run(web_api.handle_polywar_state_api(DummyRequest()))

    assert response.status == 500
    assert response.content_type == "application/json"
    assert json.loads(response.text) == {"ok": False, "error": "server_error"}
    assert "Traceback" not in response.text
    assert "database exploded" not in response.text
    assert "<html" not in response.text.lower()


def test_polywar_frontend_parser_maps_html_500_to_server_error_not_bad_json():
    js = Path("webapp/polywar.js").read_text()
    api_block = js[js.index("async function api"):js.index("function fmtTime")]

    assert "response.json" not in api_block
    assert "bad_json" not in api_block
    assert "await r.text()" in api_block
    assert 'r.status >= 500 ? "server_error" : "invalid_server_response"' in api_block
    assert "httpStatus" in api_block
    assert "console.error" in api_block
