/** Synthetic browser acceptance. No external model or real account requests. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.VELIA_PLAYWRIGHT_MODULE || 'playwright');
const bundled = (await import(process.env.VELIA_CHROMIUM_MODULE || '@sparticuz/chromium')).default;
const output = process.argv[2] || '/tmp/velia-quantum-qa';
await fs.mkdir(output, {recursive: true});
const fixture = spawn('python', [fileURLToPath(new URL('./serve-web-fixture.py', import.meta.url))],
  {env: {...process.env, VELIA_FIXTURE_QUANTUM: 'true'}, stdio: ['ignore', 'pipe', 'pipe']});
let browser;
try {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fixture startup timed out')), 15000);
    fixture.stdout.on('data', data => {
      if (String(data).includes('VELIA_WEB_FIXTURE_READY')) {clearTimeout(timer); resolve();}
    });
    fixture.once('exit', code => {clearTimeout(timer); reject(new Error('Fixture exited ' + code));});
    fixture.stderr.on('data', data => process.stderr.write(data));
  });
  browser = await chromium.launch({executablePath: process.env.VELIA_CHROMIUM_EXECUTABLE || await bundled.executablePath(),
    args: bundled.args.filter(a => !['--disable-web-security', '--allow-running-insecure-content'].includes(a)), headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 960}});
  const errors = [], calls = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (request.url().endsWith('/messages/stream')) calls.push(request.postDataJSON());});
  await page.goto('http://127.0.0.1:18180/');
  assert.equal(await page.locator('[data-model="velia-quantum"]').isDisabled(), true);
  await page.locator('#account').click();
  await page.locator('#code-ready').click();
  await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');
  await page.locator('#auth-submit').click();
  await page.locator('#auth-dialog').waitFor({state: 'hidden'});
  await page.locator('#model-button').click();
  assert.equal(await page.locator('[data-model="velia-quantum"]').isDisabled(), false);
  assert.equal(await page.locator('[data-model="velia-quantum"]').getAttribute('aria-disabled'), 'false');
  await page.locator('[data-model="velia-quantum"]').click();
  assert.equal(await page.locator('#model-label').innerText(), 'VELIA QUANTUM');
  await page.locator('#prompt').fill('Составь план проверки архитектуры');
  await page.locator('#send').click();
  await page.locator('.message-actions').waitFor();
  assert.equal(calls.at(-1).model, 'velia-quantum');
  assert.equal(await page.locator('.message-model').last().innerText(), 'QUANTUM');
  await page.reload();
  await page.locator('#model-label').filter({hasText: 'VELIA QUANTUM'}).waitFor();
  await page.locator('.message-model').filter({hasText: 'QUANTUM'}).waitFor();
  await page.screenshot({path: output + '/quantum-desktop.png'});
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#model-button').click();
  assert.match(await page.locator('#quantum-description').innerText(), /GLM-5.3 через Experiential/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await page.screenshot({path: output + '/quantum-mobile.png'});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ok: true, guestDisabled: true, quantumSelected: true,
    accountRoute: true, historyReload: true, mobile: true, providerCalls: 0}));
} finally {
  if (browser) await browser.close();
  fixture.kill('SIGTERM');
}
