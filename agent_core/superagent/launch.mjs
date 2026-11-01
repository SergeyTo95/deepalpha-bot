import { spawn, execFileSync } from 'node:child_process';
import { readFile, writeFile, mkdir, rm, chmod, rename } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { createInterface } from 'node:readline/promises';
import { once } from 'node:events';
import { configuration, PROFILE, developerPatch } from './config.mjs';
import { bootstrap, ensureProfile, managementClient, waitForPanel } from './bootstrap.mjs';
import { launchURL } from '../../desktop/src/config.mjs';
import { startProxy } from '../../desktop/src/proxy.mjs';
import { sessionManager } from '../../desktop/src/session.mjs';
import { pairDevice } from '../../desktop/src/pairing.mjs';

const desktop = resolve(import.meta.dirname, '../../desktop');
const configFile = process.argv[2];
if (!configFile || !process.argv.slice(3).every(arg => ['--check', '--pair'].includes(arg))) {
  throw new Error('Usage: node agent_core/superagent/launch.mjs <operator-config.json> [--check|--pair]');
}
const config = configuration(JSON.parse(await readFile(resolve(configFile), 'utf8')));
if (process.platform === 'win32') throw new Error('The upstream developer extension requires a Linux/macOS host; Windows clients can use its hosted integration.');
const packaged = join(desktop, '.runtime', 'package');
const runtime = existsSync(join(packaged, 'harness', 'lib', 'bin.js'))
  ? join(packaged, 'harness', 'lib', 'bin.js') : join(desktop, '.runtime', 'harness', 'apps', 'cli', 'lib', 'bin.js');
const bundle = existsSync(join(packaged, 'superagent', 'lib', 'index.js'))
  ? join(packaged, 'superagent') : join(desktop, '.runtime', 'superagent');
const version = execFileSync(process.execPath, [runtime, '--version'], { encoding: 'utf8', timeout: 30000 }).trim();
if (version !== '0.2.1-rc.3') throw new Error('Build the pinned VELIA Agent Core runtime (0.2.1-rc.3) first');
if (!existsSync(join(bundle, 'lib', 'index.js'))) throw new Error('Build the pinned developer extension first');
const account = JSON.parse(execFileSync(config.githubCLI, ['api', 'user'], { encoding: 'utf8', timeout: 30000 }));
if (!/^[A-Za-z0-9-]+$/.test(account.login || '') || config.allowedUsers.some(login => login.toLowerCase() === account.login.toLowerCase())) {
  throw new Error('githubCLI must use a dedicated agent account, separate from allowedUsers');
}
const access = JSON.parse(execFileSync(config.githubCLI, ['api', `repos/${config.repository}`], { encoding: 'utf8', timeout: 30000 }));
if (!access.permissions?.push) throw new Error('The agent account needs write access to the configured repository');
const remote = execFileSync('git', ['-C', config.checkout, 'remote', 'get-url', 'origin'], { encoding: 'utf8', timeout: 10000 }).trim();
if (![ `https://github.com/${config.repository}`, `https://github.com/${config.repository}.git` ].includes(remote)) {
  throw new Error('checkout must belong to the configured GitHub repository');
}
if (process.argv.includes('--check')) {
  console.log(JSON.stringify({ ok: true, runtime: version, repository: config.repository, agent: account.login, model: config.model }));
  process.exit(0);
}
if (process.getuid?.() === 0) throw new Error('Run the developer worker as a dedicated unprivileged OS user');
await mkdir(config.home, { recursive: true, mode: 0o700 });
await chmod(config.home, 0o700);
const sessionFile = join(config.home, 'session.json');
const storage = {
  read: async () => JSON.parse(await readFile(sessionFile, 'utf8')),
  write: async session => {
    await writeFile(sessionFile + '.tmp', JSON.stringify(session), { mode: 0o600 });
    await rename(sessionFile + '.tmp', sessionFile);
  },
};
if (process.argv.includes('--pair')) {
  const input = createInterface({ input: process.stdin, output: process.stdout });
  try { await storage.write(await pairDevice(config.gateway, await input.question('Код подключения VELIA: '))); }
  finally { input.close(); }
}
const renew = sessionManager(storage);
try { await renew(); } catch (error) {
  if (error.code === 'ENOENT') throw new Error('Pair this worker with VELIA first: add --pair and enter a device connection code');
  throw error;
}
const proxy = await startProxy(config.gateway, renew);
await ensureProfile(config.home, bundle);
const credentialFile = join(config.home, 'credentials.json'), patchFile = join(config.home, 'developer.patch.json');
await writeFile(credentialFile, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
await writeFile(patchFile, JSON.stringify(developerPatch(config, credentialFile, proxy.url)), { mode: 0o600 });
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
  ['PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'TZ', 'TMPDIR', 'XDG_CONFIG_HOME'].includes(key) || key.startsWith('LC_')));
const child = spawn(process.execPath, [runtime, '--profile', PROFILE, '--patch', patchFile,
  '--host', '127.0.0.1', '--port', '0', '--no-open'], { cwd: config.checkout,
  env: { ...env, DSH_HOME: config.home, DSH_PERMISSION_MODE: 'workspace-write' },
  detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
let closing;
async function close() {
  if (closing) return closing;
  closing = (async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit');
      process.kill(-child.pid, 'SIGTERM');
      await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 10000).unref())]);
      if (child.exitCode === null && child.signalCode === null) process.kill(-child.pid, 'SIGKILL');
    }
    await proxy.close();
    await rm(credentialFile, { force: true });
  })();
  return closing;
}
for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { void close(); });
try {
  let output = '', errors = '';
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-16000); });
  const url = await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error('Developer startup timed out: ' + errors)), 45000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', code => { clearTimeout(timer); fail(new Error(`Developer exited (${code}): ${errors}`)); });
    child.stdout.on('data', chunk => {
      output = (output + chunk.toString()).slice(-65536);
      const url = launchURL(output);
      if (url) { clearTimeout(timer); ok(url); }
    });
  });
  const call = managementClient(url);
  await waitForPanel(call);
  await bootstrap(call, config, join(import.meta.dirname, 'prepare-worktree.mjs'));
  const state = await call('GET', 'state');
  console.log(JSON.stringify({ ready: !state.setup.problem && !state.paused, panel: url,
    repository: config.repository, model: config.model, maxWorkers: state.load.maxWorkers }));
  const [code] = await once(child, 'exit');
  if (code && !closing) throw new Error(`Developer runtime stopped (${code}): ${errors}`);
} finally { await close(); }
