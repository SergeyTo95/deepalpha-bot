/** Verify the shipped Web composer selects Flash/PRO without any model calls. */
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { profilePatch, launchURL } from '../src/config.mjs';
import { startProxy } from '../src/proxy.mjs';

const { chromium } = await import(process.env.VELIA_PLAYWRIGHT_MODULE || 'playwright');
const portable = process.env.VELIA_CHROMIUM_MODULE ? (await import(process.env.VELIA_CHROMIUM_MODULE)).default : null;
const runtime = resolve(process.argv[2]);
const home = await mkdtemp(join(tmpdir(), 'velia-model-picker-'));
const workspace = join(home, 'workspace');
await mkdir(workspace);
let child, browser, page, calls = 0;
const proxy = await startProxy('https://picker-fixture.invalid/desktop-api/v1', async () => ({
  origin: 'https://picker-fixture.invalid', access_token: 'synthetic-picker-token',
}), async () => { calls++; throw Error('Model picker must not call any provider'); });
try {
  const credentials = join(home, 'credentials.json'), patch = join(home, 'patch.json');
  await writeFile(credentials, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
  await writeFile(patch, JSON.stringify(profilePatch('https://picker-fixture.invalid/desktop-api/v1', credentials, proxy.url)), { mode: 0o600 });
  child = spawn(process.execPath, [join(runtime, 'lib', 'bin.js'), '--profile', 'web', '--patch', patch,
    '--host', '127.0.0.1', '--port', '0', '--no-open'], { cwd: workspace, stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: process.env.PATH, LANG: 'C.UTF-8', DSH_HOME: join(home, 'home'), DSH_PERMISSION_MODE: 'workspace-write' } });
  let output = '', errors = '';
  child.stderr.on('data', chunk => { errors = (errors + chunk.toString()).slice(-65536); });
  const url = await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(Error('Web startup timed out: ' + errors)), 30000);
    child.once('error', e => { clearTimeout(timer); fail(e); });
    child.once('exit', code => { clearTimeout(timer); fail(Error(`Web exited (${code}): ${errors}`)); });
    child.stdout.on('data', chunk => {
      output = (output + chunk.toString()).slice(-65536);
      const value = launchURL(output);
      if (value) { clearTimeout(timer); ok(value); }
    });
  });
  browser = await chromium.launch({ headless: true,
    executablePath: process.env.VELIA_CHROMIUM_EXECUTABLE || (portable ? await portable.executablePath() : chromium.executablePath()),
    args: portable ? portable.args : ['--no-sandbox'] });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' });
  await page.goto(url);
  await page.getByRole('dialog', { name: 'Preview Notice' }).getByRole('button', { name: 'Continue', exact: true }).click();
  await page.getByRole('textbox', { name: 'Choose workspace' }).click();
  const dialog = page.getByRole('dialog', { name: 'Select Workspace Directory' });
  await dialog.getByRole('button', { name: 'Edit path' }).click();
  const input = dialog.getByRole('textbox', { name: 'Edit path' });
  await input.fill(workspace); await input.press('Enter');
  await dialog.getByRole('button', { name: 'Open', exact: true }).click();
  await page.locator('[data-composer-input][contenteditable="true"]').waitFor();
  const trigger = page.getByRole('button', { name: /Select model/ });
  assert.match(await trigger.getAttribute('title'), /^VELIA PRO/);
  const openPicker = async () => {
    await trigger.click();
    await page.getByRole('menuitem', { name: /^Model/ }).click();
  };
  await openPicker();
  const flash = page.getByRole('menuitemradio', { name: 'VELIA FLASH', exact: true });
  const pro = page.getByRole('menuitemradio', { name: 'VELIA PRO', exact: true });
  await flash.waitFor(); await pro.waitFor();
  assert.deepEqual((await page.getByRole('menuitemradio').allTextContents()).map(value => value.trim()), ['VELIA PRO', 'VELIA FLASH']);
  await flash.click();
  await page.waitForFunction(() => document.querySelector('button[title^="VELIA FLASH"]'));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('button[title^="VELIA FLASH"]'));
  assert.match(await trigger.getAttribute('title'), /^VELIA FLASH/);
  await openPicker();
  if (process.argv[3]) await page.screenshot({ path: resolve(process.argv[3]), fullPage: true });
  await pro.click();
  await page.waitForFunction(() => document.querySelector('button[title^="VELIA PRO"]'));
  assert.equal(calls, 0);
  console.log('VELIA_MODEL_PICKER_QUALIFIED ' + JSON.stringify({ flash: true, pro: true, reloadPersists: true, modelCalls: calls }));
} catch (error) {
  if (page) console.error((await page.locator('body').ariaSnapshot()).slice(0,12000));
  throw error;
} finally {
  await browser?.close();
  if (child?.exitCode === null && child.signalCode === null) {
    const exited = once(child, 'exit'); child.kill();
    await Promise.race([exited, new Promise(ok => setTimeout(ok, 4000))]);
    if (child.exitCode === null && child.signalCode === null) { child.kill('SIGKILL'); await exited; }
  }
  await proxy.close(); await rm(home, { recursive: true, force: true });
}
