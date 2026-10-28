import asyncio
import json
from types import SimpleNamespace

import pytest

import velia_quantum_transport as transport
from velia_quantum_provider import QuantumConfig, QuantumError, ZAI_URL, ZAI_FREE_MODEL
from velia_quantum_turn import stream_turn


def wire(text="Ready"):
    return ("data: " + json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]})
        + "\n\ndata: [DONE]\n\n").encode()


class Response:
    def __init__(self, status=200, code="1305", *, body=None, retry_after=None):
        self.status = self.status_code = status
        self.body = body if body is not None else (wire() if status == 200 else
            json.dumps({"error": {"code": code, "message": "secret account details"}}).encode())
        self.headers = {"Content-Type": "text/event-stream"}
        if retry_after is not None:
            self.headers["Retry-After"] = retry_after
        self.closed = False
        self.content = self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    def iter_content(self, _size):
        yield self.body

    async def iter_chunked(self, _size):
        yield self.body


class Client:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        reply = self.responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


@pytest.fixture
def setup(monkeypatch):
    transport._CIRCUITS.clear()
    clock, waits = [100.0], []
    def sleep(delay):
        waits.append(delay)
        clock[0] += delay
    async def asleep(delay):
        sleep(delay)
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(transport.asyncio, "sleep", asleep)
    monkeypatch.setattr(transport.random, "uniform", lambda _a, _b: 1.5)
    yield QuantumConfig("zai", ZAI_URL, ZAI_FREE_MODEL, "private-test-key"), clock, waits
    transport._CIRCUITS.clear()


def request(kind, client, config, *, retry=True, consume=None):
    body = {"model": config.model, "messages": [{"role": "user", "content": "private prompt"}]}
    if kind == "sync":
        with transport.completion(client, config, body, timeout=(3, 15), retry_overload=retry) as response:
            if consume:
                consume(response)
    else:
        async def run():
            async with transport.completion_async(client, config, body, retry_overload=retry) as response:
                if consume:
                    consume(response)
        asyncio.run(run())


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_one_overload_then_success_closes_response_before_wait(setup, monkeypatch, kind, caplog):
    config, clock, waits = setup
    rejected, accepted = Response(429), Response()
    client = Client(rejected, accepted)
    original = transport._delay
    # Response closure is checked at sleep time, after computing the delay.
    def sleeping(delay):
        assert rejected.closed and not accepted.closed
        waits.append(delay)
        clock[0] += delay
    async def asleep(delay):
        sleeping(delay)
    monkeypatch.setattr(transport.time, "sleep", sleeping)
    monkeypatch.setattr(transport.asyncio, "sleep", asleep)
    request(kind, client, config)
    assert len(client.calls) == 2 and waits == [1.5] and accepted.closed
    assert client.calls[0] == client.calls[1]
    assert client.calls[0][1]["headers"]["User-Agent"] == "VELIA-Quantum/1.0"
    assert client.calls[0][1]["allow_redirects"] is False
    assert "secret" not in caplog.text and "private" not in caplog.text


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_double_overload_blocks_following_calls_then_recovers(setup, kind):
    config, clock, waits = setup
    client = Client(Response(429), Response(429), Response())
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 2 and waits == [1.5]
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 2
    clock[0] += 31
    request(kind, client, config)
    assert len(client.calls) == 3 and not transport._CIRCUITS


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("status,code", [(429, "1302"), (429, "1113"), (429, "1308"),
    (401, "1000"), (500, "1305"), (429, "unknown"), (402, "card_required")])
def test_other_failures_are_never_retried_or_open_overload_circuit(setup, kind, status, code):
    config, _clock, waits = setup
    client = Client(Response(status, code), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config)
    assert len(client.calls) == 1 and waits == [] and not transport._CIRCUITS


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("retry_after", ["10", "-1", "NaN", "inf", "Wed, 28 Oct 2026 12:00:00 GMT"])
def test_retry_after_is_never_ignored_for_an_early_retry(setup, kind, retry_after):
    config, _clock, waits = setup
    client = Client(Response(429, retry_after=retry_after), Response())
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        request(kind, client, config)
    assert len(client.calls) == 1 and waits == []


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_no_retry_after_a_tool_or_stream_started(setup, kind):
    config, _clock, waits = setup
    client = Client(Response(429), Response())
    with pytest.raises(QuantumError):
        request(kind, client, config, retry=False)
    assert len(client.calls) == 1 and not waits
    client = Client(Response(), Response())
    def failed_stream(_response):
        raise QuantumError("quantum_overloaded")
    with pytest.raises(QuantumError):
        request(kind, client, config, consume=failed_stream)
    assert len(client.calls) == 1 and not waits


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_network_failure_is_never_replayed(setup, kind):
    config, _clock, waits = setup
    client = Client(OSError("private network failure"), Response())
    with pytest.raises(OSError):
        request(kind, client, config)
    assert len(client.calls) == 1 and not waits


def test_cooldown_does_not_affect_a_rotated_key(setup):
    config, _clock, waits = setup
    with pytest.raises(QuantumError):
        request("sync", Client(Response(429), Response(429)), config)
    rotated = QuantumConfig("zai", ZAI_URL, ZAI_FREE_MODEL, "rotated-key")
    client = Client(Response())
    request("sync", client, rotated)
    assert len(client.calls) == 1


def test_browser_native_turn_retries_before_emitting_text_only(setup):
    config, _clock, waits = setup
    client = Client(Response(429), Response(body=wire("こんにちは")))
    async def run():
        return [event async for event in stream_turn(client, config, [{"role": "user", "content": "hello"}])]
    assert asyncio.run(run()) == [{"text": "こんにちは"}]
    assert len(client.calls) == 2 and waits == [1.5]


def test_browser_native_tool_continuation_is_not_replayed(setup):
    config, _clock, waits = setup
    tool = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
        "function": {"name": "web_search", "arguments": '{"query":"Python","scope":"general"}'}}]},
        "finish_reason": "tool_calls"}]}
    client = Client(Response(body=("data: " + json.dumps(tool) + "\n\ndata: [DONE]\n\n").encode()), Response(429), Response())
    searches = []
    async def search(query, **kwargs):
        searches.append(query)
        return {"results": [{"title": "Docs", "url": "https://python.org", "snippet": "Public"}]}
    async def run():
        return [event async for event in stream_turn(client, config, [{"role": "user", "content": "search"}], search=search)]
    with pytest.raises(QuantumError, match="quantum_overloaded"):
        asyncio.run(run())
    assert len(client.calls) == 2 and searches == ["Python"] and not waits
