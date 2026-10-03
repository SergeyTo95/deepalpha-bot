import test from 'node:test';
import assert from 'node:assert/strict';
import { startProxy } from '../src/proxy.mjs';

test('proxy forwards streaming/tool bodies with account auth kept out of Harness', async () => {
  const account = { origin: 'https://api.example', access_token: 'va_secret' };
  const body = { model: 'velia-pro', messages: [{ role: 'tool', tool_call_id: 'call_1', content: 'file content' }], stream: true };
  const proxy = await startProxy('https://api.example/desktop-api/v1', async () => account, async (url, options) => {
    assert.equal(url, 'https://api.example/desktop-api/v1/chat/completions');
    assert.equal(options.headers.Authorization, 'Bearer va_secret');
    assert.equal(options.redirect, 'error');
    assert.deepEqual(JSON.parse(options.body), body);
    return new Response('data: {"choices":[]}\n\ndata: [DONE]\n\n', { headers: { 'Content-Type': 'text/event-stream' } });
  });
  try {
    assert.notEqual(proxy.key, account.access_token);
    const response = await fetch(proxy.url + '/chat/completions', { method: 'POST',
      headers: { Authorization: 'Bearer ' + proxy.key }, body: JSON.stringify(body) });
    assert.equal(response.status, 200); assert.match(await response.text(), /\[DONE\]/);
  } finally { await proxy.close(); }
});
test('proxy rejects strangers, unsupported routes and a mismatched account', async () => {
  let calls = 0;
  const proxy = await startProxy('https://api.example/v1', async () => ({ origin: 'https://other.example' }), () => { calls++; });
  try {
    assert.equal((await fetch(proxy.url + '/models')).status, 401);
    const headers = { Authorization: 'Bearer ' + proxy.key };
    assert.equal((await fetch(proxy.url + '/untrusted', { headers })).status, 404);
    assert.equal((await fetch(proxy.url + '/models', { headers })).status, 401);
    assert.equal(calls, 0);
  } finally { await proxy.close(); }
});
test('closing a local stream aborts the gateway request', async () => {
  let signal;
  const proxy = await startProxy('https://api.example/v1', async () => ({ origin: 'https://api.example', access_token: 'va_fake' }), async (_, options) => {
    signal = options.signal;
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode('data: start\n\n'));
      signal.addEventListener('abort', () => controller.error(new Error('cancelled')), { once: true });
    } }), { headers: { 'Content-Type': 'text/event-stream' } });
  });
  try {
    const response = await fetch(proxy.url + '/chat/completions', { method: 'POST', headers: { Authorization: 'Bearer ' + proxy.key }, body: '{}' });
    await response.body.cancel();
    for (let attempt = 0; attempt < 20 && !signal.aborted; attempt++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(signal.aborted, true);
  } finally { await proxy.close(); }
});
