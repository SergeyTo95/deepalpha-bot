import asyncio
import json
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import pytest

from desktop.game_missions import GAME_SYSTEM, PREVIEW_ORIGIN, game_plan, game_result
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
