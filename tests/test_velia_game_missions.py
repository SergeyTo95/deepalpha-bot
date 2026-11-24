import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from desktop.game_missions import GAME_REVISION_PREFIX, GAME_REVISION_SYSTEM, GAME_SYSTEM, PREVIEW_ORIGIN, game_plan, game_result, revise_game_result, saved_game_spec
from desktop.mission_runtime import MissionTools
from desktop.mission_schema import MissionError, validate_plan


@pytest.mark.parametrize('goal', [
    'Создай браузерную 2D-игру Geckoshi',
    'Велия, сделай игру про космос',
    'Build a browser game about stars',
])
def test_game_requests_use_single_bounded_tool(goal):
    assert game_plan(goal)['steps'][0]['tool'] == 'game.build'


@pytest.mark.parametrize('goal', [
    'Создай план про игру', 'Как создать игру?', 'Make a concept for a game',
])
def test_general_requests_cannot_execute_game_tool(goal):
    assert game_plan(goal) is None
    with pytest.raises(MissionError):
        validate_plan(game_plan('Создай игру'), goal)


SPEC = {'title': 'Северное сияние', 'pickup': 'Звёзды', 'theme': 'ice',
        'count': 8, 'seconds': 60, 'speed': 170}


@pytest.mark.parametrize('change', [
    {'count': True}, {'seconds': 999}, {'theme': 'https://outside.invalid/'},
    {'code': 'alert(1)'}, {'title': '<b>Игра</b>'}, {'title': '\ud800'},
])
def test_model_output_is_data_with_strict_bounds(change):
    with pytest.raises(MissionError, match='mission_game_invalid'):
        game_result(json.dumps({**SPEC, **change}), 'sample-id')


def test_selected_model_produces_an_isolated_preview_link():
    goal = 'Сделай игру про северное сияние'
    mission = {'id': 'sample-id', 'goal': goal, 'model': 'velia-quantum'}
    calls = []

    async def authorize(access, model):
        assert (access, model) == ('access', 'velia-quantum')

    async def generate(model, messages, *, max_tokens):
        assert (model, max_tokens) == ('velia-quantum', 768)
        assert messages[0] == {'role': 'system', 'content': GAME_SYSTEM}
        assert messages[1] == {'role': 'user', 'content': goal}
        return json.dumps(SPEC)

    tools = MissionTools(
        upstream=None, upstream_stream=None, authorize_model=authorize,
        handlers={'generate_background': generate,
                  'reserve': lambda user: calls.append(('reserve', user)),
                  'release': lambda user: calls.append(('release', user))},
    )
    step = game_plan(goal)['steps'][0]
    session = SimpleNamespace(user_id=7, access='access')
    result = asyncio.run(tools.execute(session, mission, step, None))

    assert calls == [('reserve', 7), ('release', 7)]
    assert result['kind'] == 'game'
    url = urlsplit(result['data']['url'])
    assert 'https://' + url.netloc == PREVIEW_ORIGIN and url.path == '/game/' and not url.query
    spec = json.loads(unquote(url.fragment.removeprefix('v1=')))
    assert {k: spec[k] for k in SPEC} == SPEC
    assert spec['v'] == 1 and 1 <= spec['seed'] <= 99999


def test_revision_keeps_unrequested_settings_and_layout():
    prior = saved_game_spec(game_result(json.dumps(SPEC), 'original-id')['url'])
    instruction = 'Увеличь таймер на 15 секунд'
    mission = {'id': 'new-id', 'goal': GAME_REVISION_PREFIX + instruction, 'model': 'velia-quantum',
               'game_revision': {'root_id': 'original-id', 'previous_specs': [prior]}}

    async def authorize(access, model):
        assert (access, model) == ('access', 'velia-quantum')

    async def generate(model, messages, *, max_tokens):
        assert messages[0]['content'] == GAME_REVISION_SYSTEM
        assert json.loads(messages[1]['content']) == {'current': prior, 'instruction': instruction}
        return '{"seconds":75}'

    tools = MissionTools(upstream=None, upstream_stream=None, authorize_model=authorize,
                         handlers={'generate_background': generate, 'reserve': lambda _: None, 'release': lambda _: None})
    step = game_plan(mission['goal'])['steps'][0]
    result = asyncio.run(tools.execute(SimpleNamespace(user_id=7, access='access'), mission, step, None))
    assert saved_game_spec(result['data']['url']) == {**prior, 'seconds': 75}
    assert prior['seconds'] == 60


@pytest.mark.parametrize('patch,error', [
    ('{}', 'mission_game_revision_no_change'),
    ('{"seconds":60}', 'mission_game_revision_no_change'),
    ('{"v":2}', 'mission_game_invalid'),
    ('{"speed":400}', 'mission_game_invalid'),
])
def test_unsupported_or_empty_game_edits_have_actionable_errors(patch, error):
    with pytest.raises(MissionError, match=error):
        revise_game_result(patch, {**SPEC, 'v': 1, 'seed': 342})


def test_revision_endpoint_links_original_game_and_github_target(tmp_path):
    import hashlib

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from desktop.mission_routes import setup_mission_routes
    from desktop.mission_store import MissionStore

    async def scenario():
        store = MissionStore(sqlite_path=tmp_path / 'games.db', session_key='x' * 48)
        original_source = {'chat_id': 'a1234567-1234-1234-1234-123456789abc', 'title': 'Игра', 'remote': False}
        goal = 'Сделай игру'
        requests = []
        receipt = {'branch': 'velia/game-example'}

        async def session(request):
            return SimpleNamespace(user_id=int(request.headers.get('X-User', '7')), access='access')

        async def identity(access):
            return {'user_id': 7}

        async def authorize(access, model):
            return None

        async def upstream(method, path, *, token, data=None):
            if path.endswith('/game-export'):
                requests.append(data)
                return 201, {'ok': True, 'export': receipt}
            return 200, {'ok': True}

        async def generate(model, messages, *, max_tokens):
            return '{"seconds":75}'

        tools = MissionTools(upstream=upstream, upstream_stream=None, authorize_model=authorize,
                             handlers={'generate_background': generate, 'reserve': lambda _: None, 'release': lambda _: None})
        app = web.Application()
        setup_mission_routes(app, session_for=session, same_origin=lambda _: True,
                             json_response=lambda data, status=200: web.json_response(data, status=status),
                             upstream=upstream, upstream_stream=None, authorize_model=authorize,
                             authenticate=identity, handlers={}, store=store, tools=tools)
        async with TestClient(TestServer(app)) as client:
            original = store.create(7, goal, game_plan(goal), 'velia-flash', 'UTC', 'create', source=original_source)
            original_result = game_result(json.dumps(SPEC), original['id'])
            prior = saved_game_spec(original_result['url'])
            claimed = store.claim(7, original['id'], 1)
            def complete(mission):
                mission.update(status='completed', lease=None, lease_until=0)
                mission['steps'][0].update(status='completed', result={'kind': 'game', 'data': original_result})
            store.checkpoint(7, original['id'], claimed['lease'], complete)
            base = '/web-api/v1/missions/'
            original_path = base + original['id'] + '/games/s1'
            account = hashlib.sha256(b':7').hexdigest()[:24]
            target = {'installation_id': 42, 'repository_id': 84}
            response = await client.post(original_path + '/github', json={'account': account, **target})
            assert response.status == 201, await response.text()
            body = {'account': account, 'model': 'velia-flash', 'request_id': 'revision', 'instruction': 'Добавь 15 секунд'}
            response = await client.post(original_path + '/revise', json=body)
            assert response.status == 201, await response.text()
            revised = (await response.json())['mission']
            assert revised['id'] != original['id'] and revised['source'] == original_source
            assert 'game_revision' not in revised
            repeat = await client.post(original_path + '/revise', json=body)
            assert (await repeat.json())['mission']['id'] == revised['id']
            for _ in range(100):
                revised = store.detail(7, revised['id'])
                if revised['status'] == 'completed':
                    break
                await asyncio.sleep(.01)
            assert revised['status'] == 'completed', revised
            revised_path = base + revised['id'] + '/games/s1'
            linked = await client.get(revised_path + '/github/target')
            assert (await linked.json())['target'] == {**target, **receipt}
            assert (await client.get(revised_path + '/github/target', headers={'X-User': '8'})).status == 404
            response = await client.post(revised_path + '/github', json={'account': account, **target})
            assert response.status == 201, await response.text()
            assert requests == [
                {**target, 'game_id': original['id'], 'spec': prior},
                {**target, 'game_id': original['id'], 'spec': {**prior, 'seconds': 75}, 'previous_specs': [prior]},
            ]
            assert store.detail(7, original['id'])['steps'][0]['result']['data'] == original_result

    asyncio.run(scenario())


def test_play_and_share_enforce_live_owner_and_recipient_permissions(tmp_path, monkeypatch):
    import hashlib

    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestClient, TestServer
    from desktop import game_proxy
    from desktop.mission_routes import setup_mission_routes
    from desktop.mission_store import MissionStore

    async def scenario():
        token = 'fixture-token-with-more-than-thirty-two-characters'
        monkeypatch.setenv('VELIA_GAME_PREVIEW_INTERNAL_TOKEN', token)
        store = MissionStore(sqlite_path=tmp_path / 'access.db', session_key='x' * 48)
        preview = web.Application()

        async def assets(request):
            assert request.headers.get('X-Velia-Game-Internal') == token
            if request.path == '/game.mjs':
                return web.Response(text='const demo = {};\ntry { launch(loadSpec()); }\n', content_type='text/javascript')
            if request.path == '/download':
                assert json.loads(request.query['spec'])['title'] == SPEC['title']
                return web.Response(body=b'zip', content_type='application/zip')
            return web.Response(text='<main><a id="game-download" href="./download">ZIP</a>\n'
                                     '<script type="module" src="./game.mjs"></script></main>',
                                content_type='text/html')

        preview.router.add_get('/{path:.*}', assets)

        async def session(request):
            value = request.headers.get('X-Test-Account')
            return SimpleNamespace(user_id=int(value), access='test-session') if value else None

        owner = {'X-Test-Account': '7'}
        recipient = {'X-Test-Account': '8'}
        stranger = {'X-Test-Account': '9'}
        account = hashlib.sha256(b':7').hexdigest()[:24]

        async with TestServer(preview) as source, ClientSession() as client_session:
            monkeypatch.setattr(game_proxy, 'ASSET_ORIGIN', str(source.make_url('/')).rstrip('/'))
            app = web.Application()
            client_key = web.AppKey('asset_client', ClientSession)
            app[client_key] = client_session
            setup_mission_routes(app, session_for=session, same_origin=lambda req: req.headers.get('Origin') == 'https://velyoncore.ai',
                                 json_response=lambda data, status=200: web.json_response(data, status=status),
                                 upstream=None, upstream_stream=None, authorize_model=None, authenticate=None,
                                 handlers={}, store=store, tools=object())
            game_proxy.setup_game_proxy(app, client_key, store=store, session_for=session)
            async with TestClient(TestServer(app)) as client:
                goal = 'Создай игру'
                mission = store.create(7, goal, game_plan(goal), 'velia-flash', 'UTC', 'private-game')
                claimed = store.claim(7, mission['id'], 1)

                def complete(record):
                    record.update(status='completed', lease=None, lease_until=0)
                    record['steps'][0].update(status='completed', result={
                        'kind': 'game', 'data': game_result(json.dumps(SPEC), mission['id']),
                    })
                store.checkpoint(7, mission['id'], claimed['lease'], complete)
                path = '/game/project/' + mission['id'] + '/s1/'
                shares = '/web-api/v1/missions/' + mission['id'] + '/games/s1/shares'
                details = await (await client.get('/web-api/v1/missions/' + mission['id'], headers=owner)).json()
                assert details['mission']['steps'][0]['result']['data']['url'] == path
                assert (await client.get(path)).status == 404
                assert (await client.get(path + 'game.mjs', headers=stranger)).status == 404
                assert (await client.get(path + 'download?spec=wrong', headers=owner)).status == 404
                assert (await client.get('/game/download?spec=old')).status == 404
                assert (await client.get('/game/game.mjs')).status == 404
                assert SPEC['title'] not in await (await client.get('/game/')).text()
                game = await client.get(path + 'game.mjs', headers=owner)
                assert game.status == 200 and game.headers['Cache-Control'] == 'private, no-store'
                assert '\\u' in await game.text()
                assert (await client.get(path + 'download', headers=owner)).status == 200
                assert (await client.get(shares, headers=stranger)).status == 404
                active = await (await client.get(shares, headers=owner)).json()
                assert active['shares'] == []
                assert (await client.post(shares, headers=owner, json={'scope': 'public', 'account': account})).status == 403
                write = {**owner, 'Origin': 'https://velyoncore.ai'}
                public = (await (await client.post(shares, headers=write, json={'scope': 'public', 'account': account})).json())['shares'][0]
                opened = await client.get(public['url'])
                assert opened.status == 200 and 'game-download' not in await opened.text()
                assert (await client.get(public['url'] + 'access')).status == 204
                assert (await client.get(public['url'] + 'download')).status == 404
                code = store.game_recipient(8)
                assert code in await (await client.get('/game/access-code', headers=recipient)).text()
                personal = (await (await client.post(shares, headers=write, json={
                    'scope': 'personal', 'recipient': code, 'account': account,
                })).json())['shares'][1]
                assert (await client.get(personal['url'])).status == 404
                assert (await client.get(personal['url'], headers=stranger)).status == 404
                assert (await client.get(personal['url'] + 'game.mjs', headers=recipient)).status == 200
                for entry, headers in ((public, {}), (personal, recipient)):
                    assert (await client.get(entry['url'] + 'access', headers=headers)).status == 204
                    revoke = await client.delete(shares + '/' + entry['id'], headers=write, json={'account': account})
                    assert revoke.status == 200
                    assert (await client.get(entry['url'], headers=headers)).status == 404
                    assert (await client.get(entry['url'] + 'access', headers=headers)).status == 404
                    assert (await client.get(entry['url'] + 'game.mjs', headers=headers)).status == 404
                assert (await client.get(path, headers=owner)).status == 200
                assert store.find(store.transaction(7), mission['id'])['game_shares'] == {}

    asyncio.run(scenario())
