"""Build validated specifications for the supported browser game genres."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import quote, unquote, urlsplit

from desktop.mission_schema import MissionError, require, validate_plan


PREVIEW_ORIGIN = 'https://velyoncore.ai'
GAME_REVISION_PREFIX = 'Сделай игру со следующей правкой: '
PREVIEW_PATHS = {
    'velyoncore.ai': '/game/',
    'velia-game-preview-production.up.railway.app': '/',
    'games.velyoncore.ai': '/',
}


def saved_game_spec(url):
    require(isinstance(url, str) and len(url) <= 1800, 'mission_game_invalid', 400)
    parts = urlsplit(url)
    require(parts.scheme == 'https' and parts.path == PREVIEW_PATHS.get(parts.netloc)
            and not parts.query and parts.fragment[:3] in {'v1=', 'v2='}, 'mission_game_invalid', 400)
    try:
        spec = json.loads(unquote(parts.fragment[3:], errors='strict'))
    except (UnicodeError, ValueError):
        raise MissionError('mission_game_invalid', 400) from None
    require(isinstance(spec, dict) and type(spec.get('v')) is int
            and parts.fragment.startswith(f"v{spec['v']}="), 'mission_game_invalid', 400)
    game_link(spec)
    return spec


GAME_INTENT = re.compile(
    r'^(?:велия[,\s]+)?(?:(?:создай|сделай|разработай|собери|напиши)\s+'
    r'(?:[\w-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:игру|мини[- ]?игру|игрушку|(?:ретро[- ]?)?шутер)|'
    r'(?:create|build|make|develop)\s+(?:[\w-]+\s+){0,5}(?:(?:2d|html5)[- ])?(?:game|mini[- ]?game|(?:retro[- ]?)?shooter|fps))(?=$|[\s,.!:?])',
    re.IGNORECASE,
)
GAME_PLAN_ONLY = re.compile(r'\b(?:план|концепци\w*|иде[яию]|сценари[йя]|plan|concept|idea)\b', re.IGNORECASE)
GAME_WORD = re.compile(r'\b(?:игру|мини[- ]?игру|игрушку|(?:ретро[- ]?)?шутер|game|mini[- ]?game|(?:retro[- ]?)?shooter|fps)\b', re.IGNORECASE)

FPS_GOAL = re.compile(
    r'\b(?:doom|wolfenstein|fps|дум(?:а|е|ом)?|boomer\s+shooter)\b|'
    r'от\s+первого\s+лица|first[- ]person|шутер\w*|\bshooter\b', re.IGNORECASE,
)
UNSUPPORTED_GENRE = re.compile(
    r'платформер|метроидвани|карточн|шахмат|шашк|покер|гонк[аиу]|гонки|'
    r'градостро|визуальн\w+\s+новелл|tower\s+defense|platformer|card\s+game|'
    r'\b(?:racing|chess|poker|mmorpg|rpg|rts|gta|minecraft)\b', re.IGNORECASE,
)


def game_kind(goal):
    if FPS_GOAL.search(goal):
        return 'unsupported' if re.search(r'вид\w*\s+сверху|от\s+третьего\s+лица|top[- ]down|third[- ]person', goal, re.I) else 'fps'
    if UNSUPPORTED_GENRE.search(goal):
        return 'unsupported'
    return 'collector'


def game_seed(mission_id):
    return int.from_bytes(hashlib.sha256(mission_id.encode()).digest()[:4], 'big') % 99999 + 1


def fps_game_result(goal, mission_id):
    match = re.search(r'«([^»]{1,54})»|"([^"]{1,54})"', goal)
    title = (match[1] or match[2]) if match else 'Сигнал из глубины'
    theme = 'ice' if re.search(r'ледян|снежн|ice|frozen', goal, re.I) else (
        'crimson' if re.search(r'лав[аыу]|красн|crimson|lava', goal, re.I) else 'industrial'
    )
    return game_link({'v': 2, 'title': title, 'theme': theme, 'difficulty': 1, 'stages': 3,
                      'seed': game_seed(mission_id)})


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


def game_plan(goal, *, previous_title=None):
    if not wants_game(goal):
        return None
    return validate_plan({
        'title': f'Доработка игры «{previous_title}»' if previous_title else ('Ретрошутер от первого лица' if game_kind(goal) == 'fps' else 'Браузерная игра'),
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


def game_json(raw):
    require(isinstance(raw, str) and len(raw) <= 2200, 'mission_game_invalid', 502)
    body = raw.strip()
    if body.startswith('```'):
        body = body.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
    try:
        return json.loads(body)
    except ValueError:
        raise MissionError('mission_game_invalid', 502) from None


def game_link(spec):
    require(isinstance(spec, dict) and type(spec.get('v')) is int and spec['v'] in {1, 2},
            'mission_game_invalid', 502)
    version = spec['v']
    if version == 1:
        require(set(spec) == {'v','title','pickup','theme','count','seconds','speed','seed'}
                and spec['theme'] in ('forest','neon','desert','ice'), 'mission_game_invalid', 502)
        ranges = [('count',5,12), ('seconds',30,120), ('speed',130,230)]
        spec = {**spec, 'pickup': checked_game_text(spec['pickup'], 21)}
    else:
        require(set(spec) == {'v','title','theme','difficulty','stages','seed'}
                and spec['theme'] in ('industrial','crimson','ice'), 'mission_game_invalid', 502)
        ranges = [('difficulty',0,2), ('stages',1,3)]
    for key, low, high in [*ranges, ('seed',1,99999)]:
        require(type(spec[key]) is int and low <= spec[key] <= high, 'mission_game_invalid', 502)
    title = checked_game_text(spec['title'], 54)
    spec = {**spec, 'title': title}
    fragment = quote(json.dumps(spec, ensure_ascii=False, separators=(',', ':')), safe='')
    require(len(fragment) + 3 <= 1700, 'mission_game_invalid', 502)
    return {'title': title, 'url': PREVIEW_ORIGIN + f'/game/#v{version}=' + fragment}


GAME_REVISION_SYSTEM = (
    'Отредактируй существующую браузерную игру Phaser. Текущие настройки и просьба пользователя '
    'придут как JSON. Верни только JSON-объект с полями, которые пользователь просит изменить. '
    'Не повторяй остальной конфиг. Поля: title (название до 54 символов), pickup (название предметов до 21 символа), '
    'theme (forest, neon, desert, ice), count (целое 5–12), seconds (целое 30–120), speed (целое 130–230), '
    'seed (целое 1–99999, меняй только по запросу на перестановку предметов). '
    'Механика, препятствия и управление фиксированы. Если запрос выходит за эти возможности или ничего '
    'не меняется, верни пустой объект {}. Не добавляй код, имена файлов, URL или новые поля.'
)


FPS_REVISION_SYSTEM = (
    'Отредактируй настройки оригинального ретрошутера. Верни только JSON с изменёнными полями: '
    'title (до 54 символов), theme (industrial, crimson, ice), difficulty (0 легко, 1 нормально, 2 сложно), '
    'stages (целое 1–3) или seed (целое 1–99999, только по запросу изменить карту). '
    'Механика неизменна. Если правка невозможна, верни {}. Не добавляй код или другие поля.'
)


def game_result(raw, mission_id):
    value = game_json(raw)
    require(isinstance(value, dict) and set(value) == {'title','pickup','theme','count','seconds','speed'},
            'mission_game_invalid', 502)
    return game_link({**value, 'v': 1, 'seed': game_seed(mission_id)})


def revise_game_result(raw, prior):
    patch = game_json(raw)
    require(isinstance(patch, dict) and set(patch) <= set(prior) - {'v'},
            'mission_game_invalid', 502)
    require(bool(patch) and any(prior[k] != value or type(prior[k]) is not type(value)
                                for k, value in patch.items()), 'mission_game_revision_no_change', 400)
    return game_link({**prior, **patch})
