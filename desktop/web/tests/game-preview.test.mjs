import test from 'node:test';
import assert from 'node:assert/strict';
import {gameDownloadLink, gamePreviewLink, wantsGame, wantsGameRevision} from '../game-preview.mjs';

test('routes game creation without intercepting questions or game plans', () => {
  for (const goal of ['Создай браузерную 2D-игру Geckoshi','Велия, сделай игру про космос','Build a browser game',
    'Создай оригинальный ретрошутер от первого лица. Добавь 5 видов монстров, 3 вида оружия с патронами, лом без патронов и 3 уровня сложности. Остальное придумай сама.',
    'Велия, собери ретро-шутер', 'Create a first-person shooter', 'Build a retro FPS']) {
    assert.equal(wantsGame(goal), true, goal);
  }
  for (const goal of ['Создай план про игру','Как создать игру?','Make a concept for a game',
    'Как создать ретрошутер?', 'Создай концепцию ретрошутера', 'Напиши план для ретрошутера',
    'Make a concept for a retro shooter']) {
    assert.equal(wantsGame(goal), false, goal);
  }
});

test('launch and ZIP use an owner-scoped URL without exposing the saved settings', () => {
  const path = '/game/project/a1234567-1234-1234-1234-123456789abc/s1/';
  const url = 'https://velyoncore.ai'+path;
  assert.equal(gamePreviewLink(path), url);
  assert.equal(gamePreviewLink(url), url);
  assert.equal(gameDownloadLink(url), url+'download');
  for (const invalid of ['javascript:alert(1)', 'https://outside.invalid'+path,
    'https://velyoncore.ai/game/#v1=%7B%7D', url+'?spec=%7B%7D', url+'#v1=1']) {
    assert.equal(gamePreviewLink(invalid), '');
    assert.equal(gameDownloadLink(invalid), '');
  }
});

test('recognizes follow-up game edits while leaving other chat requests alone', () => {
  for (const text of ['Велия, доработай игру', 'Сделай игру сложнее',
    'Добавь 15 секунд', 'Увеличь таймер до 90 секунд', 'Update this game']) {
    assert.equal(wantsGameRevision(text), true, text);
  }
  for (const text of ['Создай ещё одну игру', 'Как сделать игру сложнее?',
    'Поставь напоминание через 30 секунд']) {
    assert.equal(wantsGameRevision(text), false, text);
  }
});
