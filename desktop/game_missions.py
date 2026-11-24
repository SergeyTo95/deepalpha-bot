"""Create Phaser game specifications. Model output never becomes executable code."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit

from desktop.mission_schema import MissionError, require, validate_plan


PREVIEW_ORIGIN = 'https://velyoncore.ai'
PREVIEW_PATHS = {
    'velyoncore.ai': '/game/',
    'velia-game-preview-production.up.railway.app': '/',
    'games.velyoncore.ai': '/',
}


def saved_game_spec(url):
    require(isinstance(url, str) and len(url) <= 1800, 'mission_game_invalid', 400)
    parts = urlsplit(url)
    require(parts.scheme == 'https' and parts.path == PREVIEW_PATHS.get(parts.netloc)
            and not parts.query and parts.fragment.startswith('v1='), 'mission_game_invalid', 400)
    try:
        spec = json.loads(unquote(parts.fragment[3:], errors='strict'))
    except (UnicodeError, ValueError):
        raise MissionError('mission_game_invalid', 400) from None
    require(isinstance(spec, dict) and set(spec) == {'v','title','pickup','theme','count','seconds','speed','seed'},
            'mission_game_invalid', 400)
    return spec
GAME_INTENT = re.compile(
    r'^(?:велия[,\s]+)?(?:(?:создай|сделай|разработай|собери|напиши)\s+'
    r'(?:[\w-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:игру|мини[- ]?игру|игрушку)|'
    r'(?:create|build|make|develop)\s+(?:[\w-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:game|mini[- ]?game))(?=$|[\s,.!:?])',
    re.IGNORECASE,
)
GAME_PLAN_ONLY = re.compile(r'\b(?:план|концепци\w*|иде[яию]|сценари[йя]|plan|concept|idea)\b', re.IGNORECASE)
GAME_WORD = re.compile(r'\b(?:игру|мини[- ]?игру|игрушку|game|mini[- ]?game)\b', re.IGNORECASE)

# Adapted workflow from gamedev-skills v2.0.0 (Apache-2.0):
# phaser-core (scene and state), phaser-arcade-physics (collisions and pickups),
# game-ui-ux (readable HUD and touch controls).
GAME_SYSTEM = (
    'Ты проектируешь одну оригинальную браузерную мини-игру для готового движка Phaser 3.90. '
    'Механика фиксированная: геккон собирает предметы на одном уровне с препятствиями и таймером. '
    'Игровая сцена и физика Arcade, HUD, кнопки и перезапуск уже реализованы; придумай оформление и баланс. '
    'Учитывай жанр и настроение запроса в пределах этой механики. Не используй чужих персонажей или ассеты. '
    'Верни только JSON с шестью полями: '
    '{"title":"название до 54 символов","pickup":"название предметов во множественном числе до 21 символа",'
    '"theme":"forest","count":9,"seconds":60,"speed":185}. '
    'theme: forest, neon, desert или ice; count: целое 5–12; seconds: целое 30–120; speed: целое 130–230. '
    'Название и предметы на языке запроса. Не включай код, ссылки, координаты, HTML, личные данные или дополнительные поля.'
)


def wants_game(goal):
    match = GAME_INTENT.match(goal.strip())
    return bool(match and not GAME_PLAN_ONLY.search(GAME_WORD.split(match[0], maxsplit=1)[0]))


def game_plan(goal):
    if not wants_game(goal):
        return None
    return validate_plan({
        'title': 'Браузерная мини-игра',
        'steps': [{
            'id': 's1', 'title': 'Настроить и собрать игру',
            'tool': 'game.build', 'arguments': {},
        }],
    }, goal)


def checked_game_text(value, maximum):
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum
            and not any(c in '<>' or unicodedata.category(c) in {'Cc', 'Cf', 'Cs'} for c in value),
            'mission_game_invalid', 502)
    return value.strip()


def game_result(raw, mission_id):
    require(isinstance(raw, str) and len(raw) <= 2200, 'mission_game_invalid', 502)
    body = raw.strip()
    if body.startswith('```'):
        body = body.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:
        value = json.loads(body)
    except ValueError:
        raise MissionError('mission_game_invalid', 502) from None
    require(isinstance(value, dict) and set(value) == {'title','pickup','theme','count','seconds','speed'},
            'mission_game_invalid', 502)
    require(value['theme'] in ('forest','neon','desert','ice'), 'mission_game_invalid', 502)
    for key, low, high in [('count',5,12), ('seconds',30,120), ('speed',130,230)]:
        require(type(value[key]) is int and low <= value[key] <= high, 'mission_game_invalid', 502)
    title = checked_game_text(value['title'], 54)
    pickup = checked_game_text(value['pickup'], 21)
    seed = int.from_bytes(hashlib.sha256(mission_id.encode()).digest()[:4], 'big') % 99999 + 1
    spec = {**value, 'title': title, 'pickup': pickup, 'v': 1, 'seed': seed}
    fragment = quote(json.dumps(spec, ensure_ascii=False, separators=(',', ':')), safe='')
    require(len(fragment) + 3 <= 1700, 'mission_game_invalid', 502)
    return {'title': title, 'url': PREVIEW_ORIGIN + '/game/#v1=' + fragment}
