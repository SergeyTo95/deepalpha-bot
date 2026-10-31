"""A transient read-only release probe must still verify both browser turns."""
import asyncio

import pytest
from aiohttp import ClientConnectionError, ClientSession, web
from aiohttp.test_utils import TestServer

from desktop.probe_browser_agent_web import probe_turns


PROMPTS = ['Open the example page (read only)', 'Read the current page (read only)']


def scenario(responses):
    calls, waits = [], []
    async def run():
        async def handle(request):
            data = await request.json()
            calls.append(data)
            item = responses.pop(0)
            if item != 200:
                return web.json_response({'error': 'private details'}, status=item)
            return web.json_response({'ok': True, 'model': 'velia-flash',
                'tool_count': 1, 'text': 'Example Domain https://example.com',
                'session_id': data['session_id'], 'session_reused': data['prompt'] == PROMPTS[1]})
        app = web.Application()
        app.router.add_post('/probe', handle)
        async def wait(delay):
            waits.append(delay)
        async with TestServer(app) as server:
            async with ClientSession() as client:
                result = await probe_turns(client, server.make_url('/probe'), {}, PROMPTS, wait=wait)
                assert len(result) == 2 and result[1]['session_reused'] is True
                assert result[0]['session_id'] == result[1]['session_id']
                return result
    return run, calls, waits


def test_transient_503_restarts_with_two_verified_turns_and_no_sensitive_output(capsys):
    run, calls, waits = scenario([503, 200, 200])
    asyncio.run(run())
    assert waits == [1] and len(calls) == 3
    assert calls[0]['session_id'] != calls[1]['session_id'] == calls[2]['session_id']
    assert [c['prompt'] for c in calls] == [PROMPTS[0], *PROMPTS]
    output = capsys.readouterr().out
    assert 'attempt=2' in output and 'private' not in output
    assert all(c['session_id'] not in output for c in calls)


def test_second_turn_failure_restarts_entire_read_only_acceptance():
    run, calls, waits = scenario([200, 504, 200, 200])
    asyncio.run(run())
    assert waits == [1]
    assert [c['prompt'] for c in calls] == PROMPTS * 2
    assert calls[0]['session_id'] == calls[1]['session_id']
    assert calls[2]['session_id'] == calls[3]['session_id'] != calls[0]['session_id']


def test_persistent_failure_is_bounded_and_still_blocks_release():
    run, calls, waits = scenario([503, 502, 503])
    with pytest.raises(RuntimeError, match='unavailable_after_3_attempts'):
        asyncio.run(run())
    assert waits == [1, 2] and len(calls) == 3


@pytest.mark.parametrize('status', [400, 401, 403, 429, 500])
def test_auth_validation_quota_and_other_rejections_are_not_retried(status):
    run, calls, waits = scenario([status])
    with pytest.raises(RuntimeError, match=f'turn_1_status_{status}'):
        asyncio.run(run())
    assert not waits and len(calls) == 1


@pytest.mark.parametrize('failure', ['missing_tool', 'wrong_model', 'malformed_count'])
def test_invalid_acceptance_never_gets_a_retry_or_a_pass(failure):
    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            pass
        async def json(self):
            return {'ok': True, 'model': 'velia-pro' if failure == 'wrong_model' else 'velia-flash',
                'tool_count': '1' if failure == 'malformed_count' else 0 if failure == 'missing_tool' else 1,
                'text': 'Example Domain', 'session_id': 'fixture'}
    class Client:
        calls = 0
        def post(self, *_args, **_kwargs):
            self.calls += 1
            return Response()
    client = Client()
    with pytest.raises(RuntimeError, match='result_invalid'):
        asyncio.run(probe_turns(client, 'unused', {}, PROMPTS))
    assert client.calls == 1


def test_connection_failure_and_cancellation_have_separate_retry_behavior():
    class Client:
        def __init__(self, error):
            self.calls = 0
            self.error = error
        def post(self, *_args, **_kwargs):
            self.calls += 1
            raise self.error
    waits = []
    async def wait(delay):
        waits.append(delay)
    client = Client(ClientConnectionError('private connection details'))
    with pytest.raises(RuntimeError, match='unavailable_after_3_attempts'):
        asyncio.run(probe_turns(client, 'unused', {}, PROMPTS, wait=wait))
    assert client.calls == 3 and waits == [1, 2]
    client = Client(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(probe_turns(client, 'unused', {}, PROMPTS, wait=wait))
    assert client.calls == 1
