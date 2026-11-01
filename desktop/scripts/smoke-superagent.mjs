import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { ensureProfile, managementClient, waitForPanel, bootstrap } from '../../agent_core/superagent/bootstrap.mjs';
import { configuration, developerPatch, PROFILE } from '../../agent_core/superagent/config.mjs';
import { launchURL } from '../src/config.mjs';

const root = resolve(import.meta.dirname, '..');
const resources = process.env.VELIA_QUALIFICATION_RESOURCES || join(root, '.runtime/package');
const runtime = process.env.VELIA_SUPERAGENT_TEST_RUNTIME || join(resources, 'harness/lib/bin.js');
const bundle = process.env.VELIA_SUPERAGENT_TEST_BUNDLE || join(resources, 'superagent');
const home = await mkdtemp(join(tmpdir(), 'velia-developer-test-'));
let child;
async function stop() {
  if (child?.exitCode === null && child.signalCode === null) {
    const exited = once(child, 'exit'); child.kill();
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 5000).unref())]);
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await exited;
  }
}
try {
  const gh = join(home, 'gh-fixture');
  await writeFile(gh, `#!/bin/sh\ncase "$*" in\n` +
    `  "api user") echo '{"login":"velia-fixture-bot","id":7}' ;;\n` +
    `  "api repos/acme/widgets --jq {name: .full_name, push: .permissions.push}") echo '{"name":"acme/widgets","push":true}' ;;\n` +
    `  "api notifications"*) echo '[]' ;;\n` +
    `  "api repos/acme/widgets/pulls"*) echo '[]' ;;\n` +
    `  *) echo "Unexpected fixture command: $*" >&2; exit 1 ;;\nesac\n`, { mode: 0o700 });
  const config = configuration({ home, repository: 'acme/widgets', checkout: home, githubCLI: gh,
    allowedUsers: ['fixture-owner'], gateway: 'https://velia.example/desktop-api/v1' });
  await ensureProfile(home, resolve(bundle));
  const credentials = join(home, 'credentials.json'), patch = join(home, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: 'qualification-only' }, records: {} }), { mode: 0o600 });
  await writeFile(patch, JSON.stringify(developerPatch(config, credentials)));
  async function start() {
    child = spawn(process.execPath, [runtime, '--profile', PROFILE, '--patch', patch,
      '--host', '127.0.0.1', '--port', '0', '--no-open'], {
      cwd: home, env: { PATH: process.env.PATH, HOME: home, DSH_HOME: home, DSH_PERMISSION_MODE: 'workspace-write' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let output = '', errors = '';
    child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-16000); });
    const url = await new Promise((ok, fail) => {
      const timer = setTimeout(() => fail(new Error('Extension startup timed out: ' + errors)), 45000);
      child.once('error', error => { clearTimeout(timer); fail(error); });
      child.once('exit', code => { clearTimeout(timer); fail(new Error(`Extension exited ${code}: ${errors}`)); });
      child.stdout.on('data', chunk => {
        output = (output + chunk.toString()).slice(-65536);
        const url = launchURL(output);
        if (url) { clearTimeout(timer); ok(url); }
      });
    });
    const call = managementClient(url);
    await waitForPanel(call);
    return call;
  }
  let call = await start();
  assert.equal((await call('GET', 'state')).setup.problem.code, 'repositoryUnset');
  assert.equal(await bootstrap(call, config, resolve(import.meta.dirname, '../../agent_core/superagent/prepare-worktree.mjs')), true);
  let state = await call('GET', 'state');
  assert.equal(state.setup.problem, null);
  assert.equal(state.paused, false);
  assert.equal(state.load.maxWorkers, 1);
  let settings = await call('GET', 'settings');
  assert.equal(settings.blocks.eval.value.mode, 'off');
  assert.deepEqual(settings.blocks.policy.value.sources.github.allowed.map(user => user.id), ['fixture-owner']);
  const prompts = await call('GET', 'prompts');
  assert.match(prompts.current.config.delivery, /Never merge/);
  await call('POST', 'executor/pause', { paused: true });
  await stop();
  call = await start();
  state = await call('GET', 'state');
  assert.equal(state.setup.problem, null);
  assert.equal(state.paused, true, 'operator pause survives restart');
  assert.equal(await bootstrap(call, config, '/must-not-overwrite'), false);
  settings = await call('GET', 'settings');
  assert.equal(settings.blocks.executor.value.maxWorkers, 1);
  console.log('VELIA_SUPERAGENT_QUALIFIED: real runtime, authenticated API, dedicated profile, source setup, policy, manual merge, restart persistence');
} finally {
  await stop();
  await rm(home, { recursive: true, force: true });
}
