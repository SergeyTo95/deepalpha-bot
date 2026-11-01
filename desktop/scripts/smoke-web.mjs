import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { once } from 'node:events';
import { profilePatch, launchURL } from '../src/config.mjs';
import { qualificationRuntime } from './qualification-runtime.mjs';

const root = resolve(import.meta.dirname, '..');
const paths = qualificationRuntime(process.env.VELIA_QUALIFICATION_RESOURCES || join(root, '.runtime', 'package'));
const home = await mkdtemp(join(tmpdir(), 'velia-web-'));
let child;
try {
  const credentials = join(home, 'credentials.json'), patch = join(home, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: 'local_qualification_only' }, records: {} }), { mode: 0o600 });
  await writeFile(patch, JSON.stringify(profilePatch('https://velia.example/desktop-api/v1', paths.targetPath(credentials))), { mode: 0o600 });
  child = paths.start(['--profile', 'web', '--patch', paths.targetPath(patch), '--host', '127.0.0.1', '--port', '0', '--no-open'],
    { cwd: home, env: { ...process.env, DSH_HOME: paths.targetPath(join(home, 'home')), DSH_PERMISSION_MODE: 'workspace-write' } });
  let output = '', errors = '';
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-65536); });
  const url = await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error('Web runtime startup timed out: ' + errors)), 30000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', code => { clearTimeout(timer); fail(new Error(`Web runtime exited (${code}): ${errors}`)); });
    child.stdout.on('data', chunk => {
      output = (output + chunk.toString()).slice(-65536);
      try { const url = launchURL(output); if (url) { clearTimeout(timer); ok(url); } }
      catch (error) { clearTimeout(timer); fail(error); }
    });
  });
  let response = await fetch(url, { redirect: 'manual', signal: AbortSignal.timeout(10000) });
  const cookies = response.headers.getSetCookie().map(value => value.split(';')[0]).join('; ');
  assert.ok(cookies, 'Authenticated launch must create the session cookie');
  if (response.status === 302 || response.status === 303) {
    const location = new URL(response.headers.get('location'), url);
    assert.equal(location.origin, new URL(url).origin);
    response = await fetch(location, { headers: { Cookie: cookies }, redirect: 'manual', signal: AbortSignal.timeout(10000) });
  }
  assert.equal(response.status, 200);
  assert.match(await response.text(), /<html/i);
  console.log('VELIA_WEB_QUALIFIED', JSON.stringify({ authenticated: true, html: true, platform: paths.platform, wine: paths.wine }));
} finally {
  if (child?.exitCode === null && child.signalCode === null) {
    const exited = once(child, 'exit'); child.kill();
    await Promise.race([exited, new Promise(resolve => setTimeout(resolve, 4000))]);
    if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; }
  }
  await rm(home, { recursive: true, force: true });
}
