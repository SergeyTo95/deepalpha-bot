/** Run the shipped Harness with a live Flash gateway and a real local read tool.
 * The gateway and account token are a private operator-only loopback fixture.
 * No provider or real account credentials are given to the child runtime.
 */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { startProxy } from '../src/proxy.mjs';
import { profilePatch } from '../src/config.mjs';

const gateway = new URL(process.argv[2]);
if (gateway.protocol !== 'http:' || gateway.hostname !== '127.0.0.1' || !gateway.port) throw Error('Operator gateway must be loopback');
const token = process.argv[3];
const runtime = resolve(process.argv[4]);
const temporary = await mkdtemp(join(tmpdir(), 'velia-live-flash-'));
const workspace = join(temporary, 'workspace');
await mkdir(workspace);
const marker = 'VELIA_FLASH_REAL_READ_OK';
await writeFile(join(workspace, 'probe.txt'), marker + '\n');
let rounds = 0, readResult = false, persona = false, declaredRead = false, child, tools = 0, characters = 0;
const origin = 'https://operator-probe.invalid';
const proxy = await startProxy(origin + '/desktop-api/v1', async () => ({ origin, access_token: token }), async (url, options) => {
  if (options.body) {
    const payload = JSON.parse(options.body);
    assert.equal(payload.model, 'velia-flash');
    assert.equal(payload.max_tokens, 512, 'Flash tool requests must retain the declared output budget');
    persona ||= payload.messages.some(m => typeof m.content === 'string' && m.content.includes('Ты Велия'));
    declaredRead ||= payload.tools?.some(t => t.function?.name === 'read');
    tools = Math.max(tools, payload.tools?.length || 0);
    characters = Math.max(characters, JSON.stringify(payload).length);
    readResult ||= payload.messages.some(m => m.role === 'tool' && JSON.stringify(m.content).includes(marker));
    if (++rounds > 3) throw Error('Operator model-call budget exceeded');
  }
  const response = await fetch(new URL(new URL(url).pathname, gateway), options);
  if (!options.body || !response.body) return response;
  const round = rounds;
  let captured = '';
  return new Response(response.body.pipeThrough(new TransformStream({
    transform(chunk, controller) {
      captured = (captured + new TextDecoder().decode(chunk)).slice(-32768);
      controller.enqueue(chunk);
    },
    flush() {
      const events = captured.split('\n').filter(line => line.startsWith('data: ') && line !== 'data: [DONE]')
        .map(line => { try { return JSON.parse(line.slice(6)); } catch { return {}; } });
      console.log('VELIA_FLASH_OPERATOR_RESPONSE ' + JSON.stringify({ round, status: response.status,
        finishReasons: events.flatMap(e => (e.choices || []).map(c => c.finish_reason).filter(Boolean)),
        toolNames: events.flatMap(e => (e.choices || []).flatMap(c => (c.delta?.tool_calls || []).map(t => t.function?.name).filter(Boolean))),
        contentCharacters: events.reduce((n, e) => n + (e.choices || []).reduce((m, c) => m + (c.delta?.content?.length || 0), 0), 0) }));
    },
  })), { status: response.status, headers: response.headers });
});
try {
  const credentials = join(temporary, 'credentials.json'), patch = join(temporary, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
  const settings = profilePatch(origin + '/desktop-api/v1', credentials, proxy.url);
  settings.find(p => p.id === 'agent-default-model').config.model = 'velia-flash';
  await writeFile(patch, JSON.stringify(settings), { mode: 0o600 });
  child = spawn(process.execPath, [join(runtime, 'lib', 'bin.js'), '--profile', 'headless', '--patch', patch, '--json',
    'Прочитай файл probe.txt инструментом read. Ответь только текстом из этого файла.'], {
    cwd: workspace, stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: process.env.PATH, LANG: 'C.UTF-8', DSH_HOME: join(temporary, 'home'), DSH_PERMISSION_MODE: 'workspace-write' },
  });
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output = (output + chunk.toString()).slice(-65536); });
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-65536); });
  const code = await new Promise((ok, fail) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); fail(Error('Live Harness qualification timed out')); }, 540000);
    child.once('error', e => { clearTimeout(timer); fail(e); });
    child.once('exit', c => { clearTimeout(timer); ok(c); });
  });
  assert.equal(code, 0, errors || output || `Harness exited without output; operator rounds=${rounds}`);
  assert.ok(readResult && declaredRead && persona, 'Actual local read-tool loop must succeed');
  assert.ok(output.includes(marker), 'Final answer must contain the value read from the real file');
  console.log('VELIA_FLASH_HARNESS_PROBE ' + JSON.stringify({ ok: true, rounds, readTool: true, persona,
    declaredTools: tools, requestCharacters: characters, liveModel: true, ownerPairingVerified: false }));
} finally {
  if (child?.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  await proxy.close(); await rm(temporary, { recursive: true, force: true });
}
