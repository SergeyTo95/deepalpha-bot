/** Execute one private VELIA Agent Core browser turn with VELIA Flash only. */
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { startProxy } from '../../desktop/src/proxy.mjs';
import { profilePatch } from '../../desktop/src/config.mjs';
import { BROWSER_TOOL_ALLOWLIST } from './tool_policy.mjs';
import { extractUserAction } from './handoff.mjs';

const gateway = new URL(process.argv[2]);
if (gateway.protocol !== 'http:' || gateway.hostname !== '127.0.0.1' || !gateway.port) {
  throw new Error('VELIA Agent Core internal gateway must be loopback');
}
const token = process.argv[3];
const runtime = resolve(process.argv[4]);
const browserEndpoint = new URL(process.argv[5]);
if (browserEndpoint.protocol !== 'http:' || browserEndpoint.hostname !== '127.0.0.1'
    || !browserEndpoint.port || browserEndpoint.username || browserEndpoint.password
    || browserEndpoint.search || browserEndpoint.hash) {
  throw new Error('VELIA Agent Core browser endpoint must be loopback');
}
const sessionRoot = resolve(process.argv[6]);
const previousSessionId = process.argv[7] === '-' ? null : process.argv[7];
if (previousSessionId && !/^[A-Za-z0-9._:-]{1,160}$/.test(previousSessionId)) {
  throw new Error('browser_agent_invalid_session_id');
}
const maxPromptBytes = 12 * 1024;

let input = '';
for await (const chunk of process.stdin) {
  input += chunk.toString();
  if (Buffer.byteLength(input, 'utf8') > maxPromptBytes) throw new Error('browser_agent_prompt_too_large');
}
const prompt = input.trim();
if (!prompt) throw new Error('browser_agent_prompt_required');

await mkdir(sessionRoot, { recursive: true, mode: 0o700 });
const dshHome = join(sessionRoot, 'dsh-home');
const workspace = join(sessionRoot, 'workspace');
await mkdir(dshHome, { recursive: true, mode: 0o700 });
await mkdir(workspace, { recursive: true, mode: 0o700 });

const temporary = await mkdtemp(join(tmpdir(), 'velia-agent-core-turn-'));
const origin = 'https://velia-agent-core.internal.invalid';
let child;
let declaredTools = 0;
const proxy = await startProxy(
  origin + '/desktop-api/v1',
  async () => ({ origin, access_token: token }),
  async (url, options) => fetch(new URL(new URL(url).pathname, gateway), options),
  { allowedToolNames: BROWSER_TOOL_ALLOWLIST },
);

try {
  const credentials = join(temporary, 'credentials.json');
  const patchPath = join(temporary, 'patch.json');
  await writeFile(credentials, JSON.stringify({
    version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {},
  }), { mode: 0o600 });

  const settings = profilePatch(origin + '/desktop-api/v1', credentials, proxy.url);
  const provider = settings.find(row => row.id === 'llm-pi-ai').config.providers.velia;
  provider.models = provider.models.filter(model => model.id === 'velia-flash');
  settings.find(row => row.id === 'agent-default-model').config.model = 'velia-flash';

  const compactionPolicy = {
    thresholdRatio: 0.5,
    headroomTokens: 512,
    retainTokens: 768,
    maxTokens: 256,
    compactionRetries: 1,
    maxOverflowRetries: 1,
    auto: true,
  };
  const compaction = settings.find(row => row.id === 'compaction-basic');
  if (compaction) {
    compaction.config = compactionPolicy;
  } else {
    settings.push({
      id: 'compaction-basic',
      name: '@deepseek-ai/dsh-compaction-basic',
      config: compactionPolicy,
    });
  }

  settings.find(row => row.id === 'system-prompt').config = {
    personaPrefix: 'Ты Велия (VELIA), браузерный ИИ-агент. Говори о себе в женском роде. Выполняй веб-задачи через доступные браузерные инструменты. Продолжай работу в уже открытом браузере и учитывай его текущее состояние, включая несколько вкладок. Для новой страницы по запросу пользователя используй новую вкладку, если это сохраняет текущую работу; при просьбе вернуться используй browser_tabs и не переоткрывай страницу без необходимости. Не утверждай, что действие выполнено, пока инструмент не подтвердил результат. Не проси пользователя выполнять браузерные шаги, которые можешь выполнить сама. Никогда не выдумывай логины, пароли, OTP/TOTP/SMS-коды, recovery-коды или ответы CAPTCHA. Если сайт требует отсутствующие учётные данные, одноразовый код, passkey/security key, CAPTCHA или подтверждение на другом устройстве, остановись на текущей странице, сохрани браузерное состояние и в отдельной строке выведи ровно один маркер: VELIA_USER_ACTION_REQUIRED:credentials, VELIA_USER_ACTION_REQUIRED:otp, VELIA_USER_ACTION_REQUIRED:passkey, VELIA_USER_ACTION_REQUIRED:captcha или VELIA_USER_ACTION_REQUIRED:device_approval. Затем кратко объясни пользователю, что именно нужно сделать, не раскрывая уже введённые секреты.',
    personaSuffix: 'This is a hosted browser-only agent session. The attached Chromium belongs to this VELIA session. Local shell and host filesystem access are unavailable. Never bypass CAPTCHA, MFA, passkeys, security keys, or out-of-band device approval.',
  };
  for (const id of [
    'tool-bash', 'tool-pwsh', 'tool-fs', 'tool-fs-search', 'tool-jobs',
    'tool-schedule', 'tool-skill', 'tool-goal', 'tool-subagent-control',
    'tool-subagent-list-agents', 'tool-subagent', 'tool-subagent-fork',
    'tool-workflow', 'tool-ralph', 'tool-todo', 'tool-web',
  ]) settings.push({ id, disabled: true });
  settings.push({ insert: [
    { id: 'velia-browser-use', name: '@deepseek-ai/dsh-browser-use' },
    { id: 'velia-browser-provider', name: '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp',
      config: { mode: 'attach', endpoint: browserEndpoint.href.replace(/\/$/, ''), toolCallTimeoutMs: 45000 } },
  ] });
  await writeFile(patchPath, JSON.stringify(settings), { mode: 0o600 });

  const args = [
    join(runtime, 'lib', 'bin.js'), '--profile', 'headless', '--patch', patchPath, '--json',
  ];
  if (previousSessionId) args.push('--session-id', previousSessionId);

  child = spawn(process.execPath, args, {
    cwd: workspace,
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      PATH: process.env.PATH,
      LANG: 'C.UTF-8',
      DSH_HOME: dshHome,
      DSH_PERMISSION_MODE: 'read-only',
    },
  });
  child.stdin.end(prompt);

  let stdout = '', stderr = '';
  child.stdout.on('data', chunk => {
    stdout += chunk.toString();
    if (Buffer.byteLength(stdout, 'utf8') > 2 * 1024 * 1024) child.kill('SIGKILL');
  });
  child.stderr.on('data', chunk => {
    stderr = (stderr + chunk.toString()).slice(-65536);
  });

  const code = await new Promise((ok, fail) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      fail(new Error('browser_agent_timeout'));
    }, 420000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', exitCode => { clearTimeout(timer); ok(exitCode); });
  });

  const events = stdout.split('\n').filter(Boolean).map(line => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);
  const reportedSession = events.find(event => event.type === 'session')?.sessionId || null;
  const session = reportedSession || previousSessionId;
  const final = [...events].reverse().find(event => event.type === 'final');
  const toolCalls = events.filter(event => event.type === 'tool_call');
  declaredTools = new Set(toolCalls.map(event => event.tool)).size;

  if (code !== 0 || !final || typeof final.text !== 'string' || !session) {
    const projected = events.find(event => event.type === 'error')?.message;
    throw new Error(projected || stderr.trim().slice(-2000) || 'browser_agent_failed');
  }
  assert.ok(toolCalls.every(event => BROWSER_TOOL_ALLOWLIST.includes(event.tool)),
    'Browser Agent executed a tool outside the allowlist');

  const handoff = extractUserAction(final.text);
  process.stdout.write(JSON.stringify({
    ok: true,
    text: handoff.text,
    user_action_required: handoff.userActionRequired,
    session_id: session,
    model: 'velia-flash',
    tool_calls: toolCalls.map(event => ({ tool: event.tool, call_id: event.callId })),
    tool_count: toolCalls.length,
    distinct_tools: declaredTools,
  }) + '\n');
} finally {
  if (child?.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  await proxy.close();
  await rm(temporary, { recursive: true, force: true });
}
