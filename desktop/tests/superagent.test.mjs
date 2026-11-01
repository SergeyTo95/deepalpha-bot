import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { configuration, developerPatch, initialSettings, quote } from '../../agent_core/superagent/config.mjs';
import { ensureProfile, managementClient } from '../../agent_core/superagent/bootstrap.mjs';
import { prepareWorktree } from '../../agent_core/superagent/prepare-worktree.mjs';
import { browserAgentPatch } from '../src/config.mjs';

const value = { home: '/srv/velia-developer', checkout: '/srv/repository', githubCLI: '/srv/bin/gh-agent',
  repository: 'acme/widgets', gateway: 'https://velia.example/desktop-api/v1', allowedUsers: ['Owner'] };

test('developer configuration admits only an explicit repository, senders and existing model', () => {
  const config = configuration(value), settings = initialSettings(config, '/opt/prepare.mjs');
  assert.equal(config.model, 'velia-flash');
  assert.equal(settings.eval.mode, 'off');
  assert.equal(settings.executor.maxWorkers, 1);
  assert.deepEqual(settings.policy.sources.github.allowed, [{ id: 'Owner' }]);
  assert.equal(settings.source.config.observeComments, false);
  for (const change of [{ repository: 'acme/widgets;rm' }, { allowedUsers: [] }, { model: 'paid-fallback' },
    { home: 'relative' }, { baseBranch: 'main..other' }, { token: 'not-a-config-field' }]) {
    assert.throws(() => configuration({ ...value, ...change }));
  }
});

test('developer capabilities never enter the hosted browser profile', () => {
  const browser = browserAgentPatch(value.gateway);
  const developer = developerPatch(configuration(value), '/credentials');
  assert.equal(browser.some(row => row.id === 'dsh-superagent'), false);
  assert.equal(developer.find(row => row.id === 'dsh-superagent').config.stateDir, join(value.home, 'state'));
  assert.deepEqual(developer.find(row => row.id === 'llm-pi-ai').config.providers.velia.models.map(model => model.id), ['velia-flash']);
  assert.equal(browser.find(row => row.id === 'preset-standard').disabled, true);
});

test('profile initialization is repeatable and preserves operator settings', async t => {
  const home = await mkdtemp(join(tmpdir(), 'velia-profile-test-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  const bundle = join(home, 'bundle'); await mkdir(bundle);
  const directory = await ensureProfile(home, bundle);
  await writeFile(join(directory, 'cordis.patch.yml'), 'operator patch');
  await ensureProfile(home, bundle);
  assert.equal(await readFile(join(directory, 'cordis.patch.yml'), 'utf8'), 'operator patch');
  const manifest = join(directory, 'package.json');
  await writeFile(manifest, '{"private":true,"operator":true}');
  await assert.rejects(ensureProfile(home, bundle), /refusing to overwrite/);
  assert.match(await readFile(manifest, 'utf8'), /operator/);
});

test('management requests exchange the launch token for a cookie and never follow redirects', async () => {
  const calls = [];
  const call = managementClient('http://127.0.0.1:34567/?token=launch-secret', async (url, options) => {
    calls.push({ url: String(url), ...options });
    return calls.length === 1 ? new Response('', { status: 302, headers: {
      'set-cookie': 'dsh=session; HttpOnly', location: 'https://untrusted.example/' } }) : Response.json({ ok: true });
  });
  await call('PUT', 'settings/eval', { mode: 'off' });
  assert.equal(calls[0].redirect, 'manual');
  assert.equal(calls[1].url, 'http://127.0.0.1:34567/orchestrator/api/settings/eval');
  assert.equal(calls[1].headers.Cookie, 'dsh=session');
  assert.equal(calls[1].redirect, 'error');
  await assert.rejects(call('GET', '../outside'), /Invalid management path/);
  for (const url of ['https://example.com', 'http://localhost:8080', 'http://user:pass@127.0.0.1:1234']) {
    assert.throws(() => managementClient(url));
  }
});

test('no management mutation is sent when launch authentication fails', async () => {
  let requests = 0;
  const call = managementClient('http://127.0.0.1:12345/?token=x', async () => {
    requests++; return new Response('', { status: 403 });
  });
  await assert.rejects(call('POST', 'executor/pause', { paused: false }), /authentication failed/);
  assert.equal(requests, 1);
});

test('Ticket preparation uses an independent git worktree and rejects a second preparation', { skip: process.platform === 'win32' }, async t => {
  const root = await mkdtemp(join(tmpdir(), "velia-worktree-'test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = join(root, 'source'), remote = join(root, 'remote.git'), work = join(root, 'work');
  await mkdir(work);
  const env = { PATH: process.env.PATH, HOME: root, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
    GIT_AUTHOR_NAME: 'Fixture', GIT_AUTHOR_EMAIL: 'fixture@example.test', GIT_COMMITTER_NAME: 'Fixture',
    GIT_COMMITTER_EMAIL: 'fixture@example.test', GIT_CONFIG_COUNT: '1',
    GIT_CONFIG_KEY_0: `url.${remote}.insteadOf`, GIT_CONFIG_VALUE_0: 'https://github.com/acme/widgets.git' };
  const git = args => execFileSync('git', args, { encoding: 'utf8', env });
  git(['init', '-q', '-b', 'main', source]);
  await writeFile(join(source, 'marker.txt'), 'original\n');
  git(['-C', source, 'add', '.']); git(['-C', source, 'commit', '-qm', 'fixture']);
  git(['clone', '--bare', source, remote]);
  git(['-C', source, 'remote', 'add', 'origin', 'https://github.com/acme/widgets.git']);
  // get-url applies insteadOf, so the fixture redirects only fetch through a command wrapper.
  const gitFixture = join(root, 'bin'); await mkdir(gitFixture);
  const originalGit = execFileSync('which', ['git'], { encoding: 'utf8' }).trim();
  const wrapper = join(gitFixture, 'git');
  await writeFile(wrapper, '#!/bin/sh\n' +
    `case "$*" in *"remote get-url origin") echo 'https://github.com/acme/widgets.git';;\n` +
    `*) exec ${quote(originalGit)} "$@";; esac\n`, { mode: 0o700 });
  const ticket = { ...env, PATH: `${gitFixture}:${env.PATH}`, DSH_SUPERAGENT_DIR: join(work, 'ticket-one'), DSH_SUPERAGENT_NAME: 'ticket-one' };
  prepareWorktree(source, 'acme/widgets', 'main', ticket);
  assert.equal(await readFile(join(ticket.DSH_SUPERAGENT_DIR, 'marker.txt'), 'utf8'), 'original\n');
  await writeFile(join(ticket.DSH_SUPERAGENT_DIR, 'marker.txt'), 'changed\n');
  assert.equal(await readFile(join(source, 'marker.txt'), 'utf8'), 'original\n');
  assert.equal(git(['-C', ticket.DSH_SUPERAGENT_DIR, 'branch', '--show-current']).trim(), 'velia/ticket-one');
  assert.throws(() => prepareWorktree(source, 'acme/widgets', 'main', ticket), /must be new/);
  assert.throws(() => prepareWorktree(source, 'acme/widgets', 'main', { ...ticket, DSH_SUPERAGENT_DIR: join(source, 'nested') }), /outside/);
});
