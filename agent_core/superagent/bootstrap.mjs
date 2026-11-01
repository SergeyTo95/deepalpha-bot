import { readFile, writeFile, mkdir, symlink, realpath } from 'node:fs/promises';
import { join } from 'node:path';
import { PROFILE, initialSettings } from './config.mjs';

/** Select the extension without invoking a package manager or replacing a profile. */
export async function ensureProfile(home, bundle) {
  const directory = join(home, 'profiles', PROFILE);
  await mkdir(join(directory, 'node_modules'), { recursive: true, mode: 0o700 });
  const path = join(directory, 'package.json');
  const expected = { name: 'velia-developer-profile', private: true,
    dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app', 'dsh-superagent'] } },
    dependencies: { 'dsh-superagent': `link:${bundle}` } };
  try {
    const existing = JSON.parse(await readFile(path, 'utf8'));
    if (JSON.stringify(existing) !== JSON.stringify(expected)) throw new Error('Developer profile differs; refusing to overwrite it');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await writeFile(path, JSON.stringify(expected, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
  }
  const link = join(directory, 'node_modules', 'dsh-superagent');
  try { await symlink(bundle, link, process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) {
    if (error.code !== 'EEXIST' || await realpath(link) !== await realpath(bundle)) throw error;
  }
  return directory;
}

/** Management traffic stays on the exact launch origin and carries its token. */
export function managementClient(launchURL, fetcher = fetch) {
  const launch = new URL(launchURL);
  if (launch.protocol !== 'http:' || launch.hostname !== '127.0.0.1' || !launch.port
      || launch.username || launch.password) throw new Error('Expected a loopback developer panel');
  let authentication;
  const authenticate = async () => {
    const response = await fetcher(launch, { redirect: 'manual', signal: AbortSignal.timeout(15000) });
    const cookies = response.headers.getSetCookie().map(cookie => cookie.split(';')[0]).join('; ');
    if (![200, 302, 303].includes(response.status) || !cookies) throw new Error('Developer panel authentication failed');
    return cookies;
  };
  return async (method, path, body) => {
    if (!/^[a-zA-Z0-9_:/-]+$/.test(path) || path.includes('..')) throw new Error('Invalid management path');
    const url = new URL('/orchestrator/api/' + path, launch);
    authentication ||= authenticate();
    const cookie = await authentication;
    const response = await fetcher(url, { method, redirect: 'error', signal: AbortSignal.timeout(15000),
      headers: { 'content-type': 'application/json', 'x-orchestrator-request': '1', Cookie: cookie },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    const result = await response.json();
    if (!response.ok) throw new Error(`Developer API ${response.status}: ${result.error || 'request failed'}`);
    return result;
  };
}

export async function waitForPanel(call, { signal, attempts = 80 } = {}) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    signal?.throwIfAborted();
    try { return await call('GET', 'state'); } catch (error) {
      if (attempt === attempts - 1) throw error;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
}

/** Initialize once. Subsequent starts preserve operator changes and running work. */
export async function bootstrap(call, config, prepareScript, { signal } = {}) {
  let view = await call('GET', 'settings');
  const settings = initialSettings(config, prepareScript);
  const github = view.sources.find(source => source.id === 'github');
  if (!github) throw new Error('GitHub Source is not installed');
  if (github.revision) return false;
  const matches = (actual, expected) => Array.isArray(expected)
    ? Array.isArray(actual) && actual.length === expected.length && expected.every((value, index) => matches(actual[index], value))
    : expected && typeof expected === 'object'
      ? actual && Object.entries(expected).every(([key, value]) => matches(actual[key], value)) : actual === expected;
  // Resume a partial first-run only when every saved block still matches our
  // configuration. Never replace settings an operator has edited in the panel.
  for (const key of ['eval', 'policy', 'executor']) {
    if (view.blocks[key].revision && !matches(view.blocks[key].value, settings[key])) return false;
  }
  // Pause before the GitHub source is configured, including across its restart.
  await call('POST', 'executor/pause', { paused: true });
  for (const key of ['eval', 'policy', 'executor']) {
    if (!view.blocks[key].revision) await call('PUT', `settings/${key}`, { value: settings[key], revision: view.blocks[key].revision });
  }
  const prompts = await call('GET', 'prompts');
  if (!prompts.current.revision) await call('PUT', 'prompts', { baseRevision: prompts.current.revision, config: {
    ...prompts.current.config,
    communication: 'Ты Велия, ИИ-помощница. Говори о себе в женском роде. Отвечай по-русски, если пользователь не выбрал другой язык. Сообщай о результате или препятствии кратко.',
    delivery: 'Create a draft pull request for the assigned Ticket. Include the problem, change and validation. Never merge, enable auto-merge, deploy, publish packages or change repository settings. A person reviews and merges. Use the configured agent account and its noreply address for commits.',
  } });
  await call('PUT', 'settings/source:github', { value: settings.source, revision: github.revision });
  let state = await waitForPanel(call, { signal });
  for (let attempt = 0; state.setup.problem && attempt < 80; attempt++) {
    signal?.throwIfAborted();
    await new Promise(resolve => setTimeout(resolve, 250));
    state = await waitForPanel(call, { signal });
  }
  if (state.setup.problem) throw new Error(`Developer setup: ${state.setup.problem.detail}`);
  await call('POST', 'executor/pause', { paused: false });
  return true;
}
