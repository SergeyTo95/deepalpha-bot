/** Low-volume operator check of the public VELIA release. Run with --flash for one trial response. */
import assert from 'node:assert/strict';
import {readCompletion} from '../web/core.mjs';

const origin = new URL(process.argv.slice(2).find(arg => !arg.startsWith('--')) || 'https://velyoncore.ai').origin;
const checks = {};
const path = (value, options = {}) => fetch(origin + value, {
  ...options, cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(25000),
});
async function check(name, run) {
  try { await run(); checks[name] = 'ok'; }
  catch (error) { checks[name] = error.code && error.code !== 'ERR_ASSERTION' ? error.code : error.message; }
}

await Promise.all([
  check('home_examples', async () => {
    const home = await path('/');
    assert.equal(home.status, 200);
    const html = await home.text();
    for (const scenario of ['meeting', 'comparison', 'day']) assert.ok(html.includes('data-starter="' + scenario + '"'), 'missing_' + scenario);
    assert.ok(html.includes('id="starter-calculator"'), 'calculator_missing');
  }),
  check('public_game', async () => {
    const [page, script] = await Promise.all([path('/game/demo/'), path('/game/demo/game.mjs')]);
    assert.equal(page.status, 200);
    assert.equal(script.status, 200);
    assert.match(await script.text(), /launch\(demo\);/);
    assert.doesNotMatch(await page.text(), /id="game-download"/);
  }),
  check('guest_trial', async () => {
    const response = await path('/web-api/v1/guest');
    assert.equal(response.status, 200);
    const trial = await response.json();
    assert.equal(trial.limit, 30);
    assert.ok(trial.guest && Number.isInteger(trial.remaining), 'guest_balance_missing');
    if (!process.argv.includes('--flash')) return;
    const cookie = response.headers.getSetCookie().find(value => value.startsWith('__Host-velia-guest='))?.split(';', 1)[0];
    assert.ok(cookie && trial.remaining > 0, 'guest_limit_unavailable');
    const reply = await path('/web-api/v1/guest/chat/completions', {method: 'POST',
      headers: {Origin: origin, 'X-Velia-Request': '1', Cookie: cookie, 'Content-Type': 'application/json'},
      body: JSON.stringify({model: 'velia-flash', stream: true, web_search: false, max_tokens: 50,
        messages: [{role: 'user', content: 'Сколько будет 2 плюс 2? Ответь одним коротким предложением.'}]}),
    });
    try {
      const answer = await readCompletion(reply, () => {});
      assert.match(answer.text, /4|четыре/iu, 'guest_useful_text_missing');
      checks.flash_reply = 'ok';
    } catch (error) {
      checks.flash_reply = error.code && error.code !== 'ERR_ASSERTION' ? error.code : error.message;
      const remaining = await path('/web-api/v1/guest', {headers: {Cookie: cookie}});
      checks.flash_balance = (await remaining.json()).remaining === trial.remaining ? 'unchanged' : 'spent';
    }
  }),
]);

console.log(JSON.stringify({origin, checks}));
if (Object.entries(checks).some(([name, value]) => name !== 'flash_balance' && value !== 'ok')) process.exitCode = 1;
