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
    '''        done_received = False\n        current_limit = int(payload["max_completion_tokens"])\n''',
    '''        done_received = False\n        invalid_utf8_frame = False\n        current_limit = int(payload["max_completion_tokens"])\n''',
    "track invalid UTF-8 frame",
)
gateway = replace_once(
    gateway,
    '''                    except UnicodeDecodeError:\n                        reason = "stream_parse_error"\n                        continue\n''',
    '''                    except UnicodeDecodeError:\n                        invalid_utf8_frame = True\n                        reason = "stream_parse_error"\n                        break\n''',
    "fail current attempt on invalid UTF-8",
)
gateway = replace_once(
    gateway,
    '''                text = "".join(text_parts).strip()\n                if finish_reason == "length":\n''',
    '''                text = "".join(text_parts).strip()\n                if invalid_utf8_frame:\n                    reason = "stream_parse_error"\n                elif finish_reason == "length":\n''',
    "gate success after invalid UTF-8",
)
GATEWAY.write_text(gateway, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
tests += r'''


def test_invalid_utf8_frame_resets_partial_text_and_retries(monkeypatch):
    first = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"Частичный"},"finish_reason":null}]}',
            b"data: \xff",
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            b"data: [DONE]",
        ]
    )
    second = FakeResponse(
        [
            'data: {"choices":[{"delta":{"content":"Полный ответ"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )
    finalized = _patch_gateway(monkeypatch, [first, second])
    monkeypatch.setenv("KIMI_MAX_RETRIES", "1")
    events = []

    result = streaming.call_kimi_stream(
        prompt="USER: test invalid frame",
        feature="velia_chat",
        on_delta=lambda text: events.append(("delta", text)),
        on_reset=lambda: events.append(("reset", "")),
        request_id="request-invalid-utf8-frame",
        user_id=7,
        max_tokens=512,
    )

    assert result["ok"] is True
    assert result["text"] == "Полный ответ"
    assert events == [
        ("delta", "Частичный"),
        ("reset", ""),
        ("delta", "Полный ответ"),
    ]
    assert finalized[0][1]["status"] == "failed"
    assert finalized[0][1]["reason"] == "stream_parse_error"
    assert finalized[-1][1]["status"] == "success"
'''
TESTS.write_text(tests, encoding="utf-8")
