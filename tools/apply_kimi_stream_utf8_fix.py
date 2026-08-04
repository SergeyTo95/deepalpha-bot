from pathlib import Path

GATEWAY = Path("services/kimi_streaming_gateway.py")
TESTS = Path("tests/test_kimi_streaming_gateway.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


gateway = GATEWAY.read_text(encoding="utf-8")

gateway = replace_once(
    gateway,
    '''def _stream_delta(data: Any) -> str:\n''',
    '''def _decode_stream_line(raw_line: Any) -> str:\n    if raw_line is None:\n        return ""\n    if isinstance(raw_line, (bytes, bytearray, memoryview)):\n        return bytes(raw_line).decode("utf-8-sig")\n    return str(raw_line)\n\n\ndef _stream_delta(data: Any) -> str:\n''',
    "insert raw UTF-8 stream decoder",
)

gateway = replace_once(
    gateway,
    '''                for raw_line in response.iter_lines(decode_unicode=True):\n                    if raw_line is None:\n                        continue\n                    if isinstance(raw_line, bytes):\n                        line = raw_line.decode("utf-8", errors="replace")\n                    else:\n                        line = str(raw_line)\n                    line = line.strip()\n''',
    '''                for raw_line in response.iter_lines(decode_unicode=False):\n                    try:\n                        line = _decode_stream_line(raw_line).strip()\n                    except UnicodeDecodeError:\n                        reason = "stream_parse_error"\n                        continue\n''',
    "force raw-byte SSE iteration",
)

gateway = replace_once(
    gateway,
    '''                    delta = _stream_delta(data)\n                    if delta:\n''',
    '''                    delta = kimi_gateway._repair_utf8_mojibake(\n                        _stream_delta(data)\n                    )\n                    if delta:\n''',
    "repair streamed deltas",
)

GATEWAY.write_text(gateway, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    '''        self.headers = headers or {"x-request-id": "provider-request-1"}\n        self.closed = False\n\n    def iter_lines(self, decode_unicode=True):\n        return iter(self._lines)\n''',
    '''        self.headers = headers or {"x-request-id": "provider-request-1"}\n        self.closed = False\n        self.decode_unicode_calls = []\n\n    def iter_lines(self, decode_unicode=True):\n        self.decode_unicode_calls.append(decode_unicode)\n        return iter(self._lines)\n''',
    "record iter_lines mode",
)

tests = replace_once(
    tests,
    '''    assert response.closed is True\n    payload = streaming.kimi_gateway.requests.calls[0][1]["json"]\n''',
    '''    assert response.closed is True\n    assert response.decode_unicode_calls == [False]\n    payload = streaming.kimi_gateway.requests.calls[0][1]["json"]\n''',
    "assert raw-byte mode",
)

tests += r'''


def test_streaming_gateway_decodes_utf8_bytes_without_response_charset(monkeypatch):
    import json

    russian = "На картинке белка держит кружку пива."
    response = FakeResponse(
        [
            b"data: " + json.dumps(
                {
                    "choices": [
                        {
                            "delta": {"content": russian},
                            "finish_reason": None,
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            b"data: [DONE]",
        ],
        headers={"Content-Type": "text/event-stream"},
    )
    _patch_gateway(monkeypatch, [response])
    deltas = []

    result = streaming.call_kimi_stream(
        prompt="USER: Что изображено?",
        feature="velia_chat",
        on_delta=deltas.append,
        request_id="request-utf8-bytes",
        user_id=7,
        max_tokens=512,
    )

    assert result["ok"] is True
    assert result["text"] == russian
    assert deltas == [russian]
    assert response.decode_unicode_calls == [False]


def test_streaming_gateway_repairs_legacy_mojibake_delta(monkeypatch):
    import json

    correct = "Белка сидит на табурете."
    broken = correct.encode("utf-8").decode("latin-1")
    response = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":' +
            json.dumps(broken, ensure_ascii=False) +
            '},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )
    _patch_gateway(monkeypatch, [response])
    deltas = []

    result = streaming.call_kimi_stream(
        prompt="USER: test",
        feature="velia_chat",
        on_delta=deltas.append,
        request_id="request-mojibake",
        user_id=7,
        max_tokens=512,
    )

    assert result["ok"] is True
    assert result["text"] == correct
    assert deltas == [correct]
'''

TESTS.write_text(tests, encoding="utf-8")
