import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { startProxy } from '../src/proxy.mjs';
import { profilePatch } from '../src/config.mjs';
import { runtimePaths } from '../src/runtime.mjs';

const root = resolve(import.meta.dirname, '..');
const paths = runtimePaths({ packaged: true, resources: process.env.VELIA_QUALIFICATION_RESOURCES || join(root, '.runtime', 'package'), platform: process.platform });
const temporary = await mkdtemp(join(tmpdir(), 'velia-runtime-'));
const workspace = join(temporary, 'workspace');
const { mkdir } = await import('node:fs/promises');
await mkdir(workspace);
await writeFile(join(workspace, 'smoke.txt'), 'VELIA_LOCAL_FILE_OK\n');
let rounds = 0, toolResult = false, child;
const proxy = await startProxy('https://velia.example/desktop-api/v1', async () => ({ origin: 'https://velia.example', access_token: 'va_test_only' }), async (_, options) => {
  const payload = JSON.parse(options.body);
  assert.ok(payload.messages.some(message => typeof message.content === 'string' && message.content.includes('Ты Велия')));
  const result = payload.messages.find(message => message.role === 'tool');
  let delta, reason;
  if (!result) {
    const tool = payload.tools?.find(tool => tool.function.name === 'read');
    assert.ok(tool, 'The installed read tool must reach the provider');
    delta = { role: 'assistant', content: null, tool_calls: [{ index: 0, id: 'call_velia_read', type: 'function',
      function: { name: 'read', arguments: JSON.stringify({ file_path: join(workspace, 'smoke.txt') }) } }] };
    reason = 'tool_calls';
  } else {
    assert.equal(result.tool_call_id, 'call_velia_read');
    assert.ok(JSON.stringify(result.content).includes('VELIA_LOCAL_FILE_OK'));
    toolResult = true; delta = { role: 'assistant', content: 'Прочитала файл. VELIA_LOCAL_FILE_OK' }; reason = 'stop';
  }
  rounds++;
  const chunk = value => 'data: ' + JSON.stringify({ id: 'chatcmpl_velia_smoke', object: 'chat.completion.chunk',
    created: 1, model: 'velia-pro', choices: [{ index: 0, ...value }] }) + '\n\n';
  return new Response(chunk({ delta, finish_reason: null }) + chunk({ delta: {}, finish_reason: reason }) + 'data: [DONE]\n\n',
    { headers: { 'Content-Type': 'text/event-stream' } });
});
try {
  const credentials = join(temporary, 'credentials.json');
  const patch = join(temporary, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
  await writeFile(patch, JSON.stringify(profilePatch('https://velia.example/desktop-api/v1', credentials, proxy.url)), { mode: 0o600 });
  child = spawn(paths.node, [paths.bin, '--profile', 'headless', '--patch', patch, 'Прочитай smoke.txt и кратко ответь по содержимому.'],
    { cwd: workspace, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, DSH_HOME: join(temporary, 'home'), DSH_PERMISSION_MODE: 'workspace-write' } });
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output = (output + chunk.toString()).slice(-65536); });
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-65536); });
  const code = await new Promise((ok, fail) => {
    const timer = setTimeout(() => { child.kill(); fail(new Error('Installed runtime qualification timed out')); }, 45000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', code => { clearTimeout(timer); ok(code); });
  });
  assert.equal(code, 0, errors);
  assert.equal(rounds, 2, errors || output); assert.equal(toolResult, true);
  assert.ok(output.includes('VELIA_LOCAL_FILE_OK'), output);
  console.log('VELIA_RUNTIME_QUALIFIED', JSON.stringify({ rounds, readTool: true, persona: true, accountProxy: true }));
} finally {
  if (child?.exitCode === null && child.signalCode === null) child.kill();
  await proxy.close(); await rm(temporary, { recursive: true, force: true });
}
