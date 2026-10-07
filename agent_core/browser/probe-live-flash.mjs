/** VELIA Agent Core browser acceptance: real Flash -> real Playwright browser tool -> real page marker. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { startProxy } from '../../desktop/src/proxy.mjs';
import { profilePatch } from '../../desktop/src/config.mjs';
import { BROWSER_TOOL_ALLOWLIST } from './tool_policy.mjs';

const gateway = new URL(process.argv[2]);
if (gateway.protocol !== 'http:' || gateway.hostname !== '127.0.0.1' || !gateway.port) {
  throw Error('Operator gateway must be loopback');
}
const token = process.argv[3];
const runtime = resolve(process.argv[4]);
const chromium = resolve(process.argv[5] || '/usr/bin/chromium');
const temporary = await mkdtemp(join(tmpdir(), 'velia-agent-core-browser-'));
const marker = 'VELIA_AGENT_CORE_BROWSER_FLASH_OK';
let child, fixture;
let rounds = 0, browserDeclared = false, browserResult = false, persona = false, maxTools = 0;

const fixtureServer = createServer((request, response) => {
  if (request.url !== '/proof') {
    response.writeHead(404, { 'Content-Type': 'text/plain' }); response.end('not found'); return;
  }
  response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(`<!doctype html><html><body><main><h1>VELIA Browser Proof</h1><p id="proof">${marker}</p></main></body></html>`);
});
fixtureServer.listen(0, '127.0.0.1');
await once(fixtureServer, 'listening');
const fixtureURL = `http://127.0.0.1:${fixtureServer.address().port}/proof`;

const origin = 'https://operator-probe.invalid';
const proxy = await startProxy(origin + '/desktop-api/v1', async () => ({ origin, access_token: token }), async (url, options) => {
  if (options.body) {
    const payload = JSON.parse(options.body);
    assert.equal(payload.model, 'velia-flash', 'Browser Agent must be Flash-only');
    assert.ok(payload.max_tokens <= 512, 'Flash answer budget must remain bounded');
    persona ||= payload.messages.some(m => typeof m.content === 'string' && m.content.includes('Велия'));
    const names = (payload.tools || []).map(tool => tool.function?.name || '');
    browserDeclared ||= names.some(name => name.startsWith('mcp__playwright-mcp__'));
    maxTools = Math.max(maxTools, names.length);
    browserResult ||= payload.messages.some(m => m.role === 'tool' && JSON.stringify(m.content).includes(marker));
    if (++rounds > 7) throw Error('Browser Agent model-call budget exceeded');
  }
  return fetch(new URL(new URL(url).pathname, gateway), options);
}, { allowedToolNames: BROWSER_TOOL_ALLOWLIST });

try {
  const credentials = join(temporary, 'credentials.json');
  const patchPath = join(temporary, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
  const settings = profilePatch(origin + '/desktop-api/v1', credentials, proxy.url);
  settings.find(row => row.id === 'llm-pi-ai').config.providers.velia.models =
    settings.find(row => row.id === 'llm-pi-ai').config.providers.velia.models.filter(model => model.id === 'velia-flash');
  settings.find(row => row.id === 'agent-default-model').config.model = 'velia-flash';
  settings.find(row => row.id === 'system-prompt').config.personaPrefix =
    'Ты Велия (VELIA), браузерный ИИ-агент. Используй браузерные инструменты и не выдумывай результаты.';
  for (const id of ['tool-bash', 'tool-pwsh', 'tool-fs', 'tool-fs-search', 'tool-jobs',
    'tool-schedule', 'tool-skill', 'tool-goal', 'tool-subagent-control', 'tool-subagent-list-agents',
    'tool-subagent', 'tool-subagent-fork', 'tool-workflow', 'tool-ralph', 'tool-todo', 'tool-web']) {
    settings.push({ id, disabled: true });
  }
  settings.push({ insert: [
    { id: 'velia-browser-use', name: '@deepseek-ai/dsh-browser-use' },
    { id: 'velia-browser-provider', name: '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp',
      config: { mode: 'launch', headless: true, executablePath: chromium, toolCallTimeoutMs: 45000 } },
  ] });
  await writeFile(patchPath, JSON.stringify(settings), { mode: 0o600 });

  child = spawn(process.execPath, [join(runtime, 'lib', 'bin.js'), '--profile', 'headless', '--patch', patchPath, '--json',
    `Открой браузером ${fixtureURL}. Найди значение внутри элемента #proof и ответь только этим значением.`], {
    cwd: temporary,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: process.env.PATH, LANG: 'C.UTF-8', DSH_HOME: join(temporary, 'home'), DSH_PERMISSION_MODE: 'read-only' },
  });
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output = (output + chunk.toString()).slice(-131072); });
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-131072); });
  const code = await new Promise((ok, fail) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); fail(Error('VELIA Browser Agent qualification timed out')); }, 420000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', exitCode => { clearTimeout(timer); ok(exitCode); });
  });
  assert.equal(code, 0, errors || output || 'Agent Core exited without output');
  assert.ok(browserDeclared, 'Playwright MCP browser tools were not declared to Flash');
  assert.ok(maxTools <= BROWSER_TOOL_ALLOWLIST.length, 'Browser Agent exposed more tools than the allowlist');
  assert.ok(browserResult, 'Flash never received the real browser result');
  assert.ok(persona, 'VELIA browser persona was not sent');
  assert.ok(output.includes(marker), 'Final answer must contain the browser-read marker');
  console.log('VELIA_AGENT_CORE_BROWSER_PROBE ' + JSON.stringify({
    ok: true, model: 'velia-flash', browser: 'playwright-mcp', liveModel: true,
    browserResult: true, rounds, declaredTools: maxTools, paidFallback: false,
  }));
} finally {
  if (child?.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  await proxy.close();
  fixtureServer.closeAllConnections?.();
  await new Promise(resolvePromise => fixtureServer.close(resolvePromise));
  await rm(temporary, { recursive: true, force: true });
}
