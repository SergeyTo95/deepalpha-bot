from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:120]!r}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


# Kimi responses must be decoded from raw UTF-8 bytes. requests.Response.json()
# can honor an incorrect provider charset and produce visible mojibake.
replace_once(
    "services/kimi_gateway.py",
    "import base64\nimport logging\n",
    "import base64\nimport json\nimport logging\n",
)
replace_once(
    "services/kimi_gateway.py",
    "\ndef _extract_final_text(data: Any) -> str:\n",
    '''\ndef _decode_json_response(response: Any) -> Any:\n    raw = getattr(response, "content", None)\n    if isinstance(raw, (bytes, bytearray, memoryview)) and raw:\n        try:\n            return json.loads(bytes(raw).decode("utf-8-sig"))\n        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):\n            pass\n    return response.json()\n\n\ndef _repair_utf8_mojibake(value: str) -> str:\n    text = str(value or "")\n    suspicious = sum(text.count(marker) for marker in ("Ð", "Ñ", "Ã", "Â"))\n    if suspicious < 2:\n        return text\n    try:\n        repaired = text.encode("latin-1").decode("utf-8")\n    except (UnicodeEncodeError, UnicodeDecodeError):\n        return text\n    repaired_suspicious = sum(\n        repaired.count(marker) for marker in ("Ð", "Ñ", "Ã", "Â")\n    )\n    return repaired if repaired_suspicious < suspicious else text\n\n\ndef _extract_final_text(data: Any) -> str:\n''',
)
replace_once(
    "services/kimi_gateway.py",
    '''    if isinstance(content, str):\n        return content.strip()\n''',
    '''    if isinstance(content, str):\n        return _repair_utf8_mojibake(content).strip()\n''',
)
replace_once(
    "services/kimi_gateway.py",
    '''        return "".join(chunks).strip()\n''',
    '''        return _repair_utf8_mojibake("".join(chunks)).strip()\n''',
)
replace_once(
    "services/kimi_gateway.py",
    "                    data = response.json()\n",
    "                    data = _decode_json_response(response)\n",
)

# Expose only authenticated image bytes. Metadata/history remain byte-free.
replace_once(
    "services/velia_attachment_service.py",
    "\ndef delete_attachment(user_id: int, attachment_id: str) -> bool:\n",
    '''\ndef get_attachment_content(\n    user_id: int,\n    attachment_id: str,\n) -> Optional[Dict[str, Any]]:\n    conn = get_connection()\n    cursor = conn.cursor()\n    try:\n        cursor.execute(\n            """\n            SELECT attachment_id, mime_type, kind, byte_size, content_bytes,\n                   width, height\n            FROM velia_attachments\n            WHERE attachment_id=%s AND user_id=%s\n              AND kind='image'\n              AND extraction_status='ready'\n              AND deleted_at IS NULL\n            LIMIT 1\n            """,\n            (str(attachment_id), int(user_id)),\n        )\n        row = cursor.fetchone()\n        if not row:\n            return None\n        if isinstance(row, dict):\n            attachment_value = row.get("attachment_id")\n            mime_type = row.get("mime_type")\n            kind = row.get("kind")\n            byte_size = row.get("byte_size")\n            content_bytes = row.get("content_bytes")\n            width = row.get("width")\n            height = row.get("height")\n        else:\n            (\n                attachment_value,\n                mime_type,\n                kind,\n                byte_size,\n                content_bytes,\n                width,\n                height,\n            ) = row\n        raw = bytes(content_bytes or b"")\n        if not raw or str(kind or "") != "image":\n            return None\n        return {\n            "id": str(attachment_value or ""),\n            "mime_type": str(mime_type or ""),\n            "kind": "image",\n            "byte_size": int(byte_size or len(raw)),\n            "content_bytes": raw,\n            "width": int(width or 0) or None,\n            "height": int(height or 0) or None,\n        }\n    finally:\n        cursor.close()\n        conn.close()\n\n\ndef delete_attachment(user_id: int, attachment_id: str) -> bool:\n''',
)

replace_once(
    "velia_mobile_attachment_routes.py",
    '''from services.velia_attachment_service import (\n    AttachmentError,\n    get_attachment,\n)\n''',
    '''from services.velia_attachment_service import (\n    AttachmentError,\n    get_attachment,\n    get_attachment_content,\n)\n''',
)
replace_once(
    "velia_mobile_attachment_routes.py",
    '''_ALLOWED_MIME_TYPES = {\n    "image/jpeg",\n    "image/png",\n    "image/webp",\n    "application/pdf",\n    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",\n    "text/plain",\n}\n''',
    '''_ALLOWED_IMAGE_MIME_TYPES = {\n    "image/jpeg",\n    "image/png",\n    "image/webp",\n}\n_ALLOWED_MIME_TYPES = _ALLOWED_IMAGE_MIME_TYPES | {\n    "application/pdf",\n    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",\n    "text/plain",\n}\n''',
)
replace_once(
    "velia_mobile_attachment_routes.py",
    "\n    async def handle_attachment_delete(request: web.Request) -> web.Response:\n",
    '''\n    async def handle_attachment_content(request: web.Request) -> web.Response:\n        unavailable_error = _attachment_api_unavailable_error()\n        if unavailable_error:\n            return _unavailable_response(unavailable_error)\n        _scrub_legacy_payloads_best_effort()\n        auth = _require_mobile_auth(request)\n        if not auth:\n            return _json_response({"ok": False, "error": "unauthorized"}, status=401)\n        attachment = await asyncio.to_thread(\n            get_attachment_content,\n            int(auth["user_id"]),\n            str(request.match_info.get("attachment_id") or ""),\n        )\n        if not attachment:\n            return _json_response(\n                {"ok": False, "error": "attachment_not_found"},\n                status=404,\n            )\n        mime_type = str(attachment.get("mime_type") or "").strip().lower()\n        content = bytes(attachment.get("content_bytes") or b"")\n        if mime_type not in _ALLOWED_IMAGE_MIME_TYPES or not content:\n            return _json_response(\n                {"ok": False, "error": "attachment_not_found"},\n                status=404,\n            )\n        response = web.Response(body=content, status=200, content_type=mime_type)\n        response.headers["Cache-Control"] = "private, no-store, max-age=0"\n        response.headers["Pragma"] = "no-cache"\n        response.headers["X-Content-Type-Options"] = "nosniff"\n        response.headers["Content-Disposition"] = "inline"\n        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"\n        return response\n\n    async def handle_attachment_delete(request: web.Request) -> web.Response:\n''',
)
replace_once(
    "velia_mobile_attachment_routes.py",
    '''    app.router.add_delete(\n        "/mobile-api/v1/attachments/{attachment_id}",\n        handle_attachment_delete,\n    )\n''',
    '''    app.router.add_get(\n        "/mobile-api/v1/attachments/{attachment_id}/content",\n        handle_attachment_content,\n    )\n    app.router.add_delete(\n        "/mobile-api/v1/attachments/{attachment_id}",\n        handle_attachment_delete,\n    )\n''',
)

Path("tests/test_velia_attachment_utf8_preview.py").write_text(
    '''import asyncio\nimport json\nfrom types import SimpleNamespace\n\nfrom services import kimi_gateway\nfrom services import velia_attachment_service\nimport velia_mobile_attachment_routes as routes\n\n\nclass _WrongCharsetResponse:\n    status_code = 200\n    headers = {}\n\n    def __init__(self):\n        self.content = json.dumps(\n            {\n                "choices": [\n                    {\n                        "message": {"content": "На фото кот"},\n                        "finish_reason": "stop",\n                    }\n                ]\n            },\n            ensure_ascii=False,\n        ).encode("utf-8")\n\n    def json(self):\n        return json.loads(self.content.decode("latin-1"))\n\n\ndef test_kimi_json_decoder_ignores_wrong_provider_charset():\n    data = kimi_gateway._decode_json_response(_WrongCharsetResponse())\n    assert kimi_gateway._extract_final_text(data) == "На фото кот"\n\n\ndef test_kimi_text_repair_recovers_common_utf8_mojibake():\n    broken = "На фото кот".encode("utf-8").decode("latin-1")\n    assert kimi_gateway._repair_utf8_mojibake(broken) == "На фото кот"\n    assert kimi_gateway._repair_utf8_mojibake("Обычный русский текст") == (\n        "Обычный русский текст"\n    )\n\n\nclass _Cursor:\n    def __init__(self, row):\n        self.row = row\n        self.executed = []\n        self.closed = False\n\n    def execute(self, sql, params):\n        self.executed.append((sql, params))\n\n    def fetchone(self):\n        return self.row\n\n    def close(self):\n        self.closed = True\n\n\nclass _Connection:\n    def __init__(self, row):\n        self.cursor_value = _Cursor(row)\n        self.closed = False\n\n    def cursor(self):\n        return self.cursor_value\n\n    def close(self):\n        self.closed = True\n\n\ndef test_attachment_content_is_owner_scoped_and_returns_original_image(monkeypatch):\n    connection = _Connection(\n        (\n            "attachment-1",\n            "image/png",\n            "image",\n            7,\n            memoryview(b"pngdata"),\n            120,\n            80,\n        )\n    )\n    monkeypatch.setattr(velia_attachment_service, "get_connection", lambda: connection)\n\n    result = velia_attachment_service.get_attachment_content(42, "attachment-1")\n\n    assert result == {\n        "id": "attachment-1",\n        "mime_type": "image/png",\n        "kind": "image",\n        "byte_size": 7,\n        "content_bytes": b"pngdata",\n        "width": 120,\n        "height": 80,\n    }\n    sql, params = connection.cursor_value.executed[0]\n    assert "user_id=%s" in sql\n    assert "kind='image'" in sql\n    assert params == ("attachment-1", 42)\n    assert connection.cursor_value.closed is True\n    assert connection.closed is True\n\n\nclass _Router:\n    def __init__(self):\n        self.get = {}\n        self.post = {}\n        self.delete = {}\n\n    def add_get(self, path, handler):\n        self.get[path] = handler\n\n    def add_post(self, path, handler):\n        self.post[path] = handler\n\n    def add_delete(self, path, handler):\n        self.delete[path] = handler\n\n\nclass _App:\n    def __init__(self):\n        self.router = _Router()\n\n\ndef test_authenticated_content_route_returns_no_store_image_bytes(monkeypatch):\n    app = _App()\n    monkeypatch.setattr(routes, "_scrub_legacy_payloads_best_effort", lambda: None)\n    monkeypatch.setattr(routes, "_attachment_api_unavailable_error", lambda: "")\n    monkeypatch.setattr(routes, "_require_mobile_auth", lambda _request: {"user_id": 42})\n    monkeypatch.setattr(\n        routes,\n        "get_attachment_content",\n        lambda user_id, attachment_id: {\n            "id": attachment_id,\n            "mime_type": "image/png",\n            "kind": "image",\n            "byte_size": 7,\n            "content_bytes": b"pngdata",\n            "width": 120,\n            "height": 80,\n        }\n        if user_id == 42\n        else None,\n    )\n    routes.setup_velia_mobile_attachment_routes(app)\n    handler = app.router.get[\n        "/mobile-api/v1/attachments/{attachment_id}/content"\n    ]\n    request = SimpleNamespace(match_info={"attachment_id": "attachment-1"})\n\n    response = asyncio.run(handler(request))\n\n    assert response.status == 200\n    assert response.body == b"pngdata"\n    assert response.content_type == "image/png"\n    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"\n    assert response.headers["X-Content-Type-Options"] == "nosniff"\n''',
    encoding="utf-8",
)

print("Applied VELIA UTF-8 and attachment preview backend patch")
