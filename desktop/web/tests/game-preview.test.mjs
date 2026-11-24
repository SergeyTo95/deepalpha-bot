import test from 'node:test';
import assert from 'node:assert/strict';
import {gameDownloadLink, gamePreviewLink, wantsGame} from '../game-preview.mjs';

test('routes game creation without intercepting questions or game plans', () => {
  for (const goal of ['Создай браузерную 2D-игру Geckoshi','Велия, сделай игру про космос','Build a browser game']) {
    assert.equal(wantsGame(goal), true, goal);
  }
  for (const goal of ['Создай план про игру','Как создать игру?','Make a concept for a game']) {
    assert.equal(wantsGame(goal), false, goal);
  }
});

test('allows only game links on the dedicated preview origin', () => {
  const link = 'https://velia-game-preview-production.up.railway.app/#v1=%7B%7D';
  const branded = 'https://velyoncore.ai/game/#v1=%7B%7D';
  assert.equal(gamePreviewLink(link), branded);
  assert.equal(gamePreviewLink(branded), branded);
  assert.equal(gameDownloadLink(link), 'https://velyoncore.ai/game/download?spec=%7B%7D');
  for (const invalid of ['javascript:alert(1)', 'https://outside.invalid/#v1=1',
    link.replace('/#', '/login#'), link.replace('/#', '/?query=1#'), link.replace('#v1=', '#wrong=')]) {
    assert.equal(gamePreviewLink(invalid), '');
  }
});
