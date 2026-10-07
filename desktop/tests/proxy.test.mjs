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
test('Flash restores the SDK one-token tool budget while preserving other requests', async () => {
  const seen = [];
  const proxy = await startProxy('https://api.example/desktop-api/v1',
    async () => ({ origin: 'https://api.example', access_token: 'va_fixture' }), async (_, options) => {
      seen.push(JSON.parse(options.body));
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } });
    });
  const template = { messages: [{ role: 'user', content: 'Прочитай probe.txt' }], stream: true,
    tools: [{ type: 'function', function: { name: 'read', parameters: { type: 'object', properties: { file_path: { type: 'string' } } } } }] };
  const cases = [
    { ...template, model: 'velia-flash', max_tokens: 1 },
    { ...template, model: 'velia-flash', max_tokens: 128 },
    { ...template, model: 'velia-pro', max_tokens: 1 },
    { ...template, tools: [], model: 'velia-flash', max_tokens: 1 },
  ];
  try {
    for (const body of cases) {
      const response = await fetch(proxy.url + '/chat/completions', { method: 'POST',
        headers: { Authorization: 'Bearer ' + proxy.key }, body: JSON.stringify(body) });
      assert.equal(response.status, 200); await response.text();
    }
    assert.deepEqual(seen, [{ ...cases[0], max_tokens: 512 }, ...cases.slice(1)]);
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


test('Flash Agent Core can expose only an explicit browser-tool allowlist', async () => {
  let seen;
  const proxy = await startProxy(
    'https://api.example/desktop-api/v1',
    async () => ({ origin: 'https://api.example', access_token: 'va_fixture' }),
    async (_, options) => {
      seen = JSON.parse(options.body);
      return new Response('{}', { headers: { 'Content-Type': 'application/json' } });
    },
    { allowedToolNames: ['mcp__playwright-mcp__browser_navigate', 'mcp__playwright-mcp__browser_snapshot'] },
  );
  const body = {
    model: 'velia-flash', max_tokens: 1, messages: [{ role: 'user', content: 'Open example.com' }],
    tools: [
      { type: 'function', function: { name: 'mcp__playwright-mcp__browser_navigate', parameters: { type: 'object' } } },
      { type: 'function', function: { name: 'mcp__playwright-mcp__browser_snapshot', parameters: { type: 'object' } } },
      { type: 'function', function: { name: 'mcp__playwright-mcp__browser_run_code_unsafe', parameters: { type: 'object' } } },
      { type: 'function', function: { name: 'read', parameters: { type: 'object' } } },
    ],
  };
  try {
    const response = await fetch(proxy.url + '/chat/completions', {
      method: 'POST', headers: { Authorization: 'Bearer ' + proxy.key }, body: JSON.stringify(body),
    });
    assert.equal(response.status, 200);
    await response.text();
    assert.deepEqual(seen.tools.map(tool => tool.function.name), [
      'mcp__playwright-mcp__browser_navigate',
      'mcp__playwright-mcp__browser_snapshot',
    ]);
    assert.equal(seen.max_tokens, 512);
  } finally { await proxy.close(); }
});
