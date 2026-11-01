import { app, BrowserWindow, ipcMain, safeStorage } from 'electron';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { ownedPage } from '../src/ipc.mjs';
import { sessionVault } from '../src/vault.mjs';

const root = resolve(import.meta.dirname, '..');
const home = await mkdtemp(join(tmpdir(), 'velia-electron-'));
app.setPath('userData', join(home, 'electron'));
const deadline = setTimeout(() => { console.error('VELIA UI qualification timed out'); app.exit(1); }, 30000);
let window;
try {
  await app.whenReady();
  const page = join(root, 'ui', 'connect.html');
  window = new BrowserWindow({ width: 700, height: 800, show: false,
    webPreferences: { preload: join(root, 'src', 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true } });
  const trusted = event => ownedPage(event, window.webContents, pathToFileURL(page).href);
  ipcMain.handle('velia:open-pairing', event => { assert.ok(trusted(event)); return true; });
  ipcMain.handle('velia:pair', (event, code) => {
    assert.ok(trusted(event)); assert.equal(code, 'ABCD-EFGH-JKLM-NPQR');
    return { ok: false, error: 'Тест подключения: повторите код.' };
  });
  await window.loadFile(page);
  const isolated = await window.webContents.executeJavaScript('typeof require === "undefined" && typeof window.veliaConnect.submit === "function"');
  assert.equal(isolated, true);
  await window.webContents.executeJavaScript(`document.getElementById('code').value = 'ABCD-EFGH-JKLM-NPQR'; document.getElementById('connect').requestSubmit()`);
  for (let attempt = 0; attempt < 30; attempt++) {
    if (await window.webContents.executeJavaScript(`document.getElementById('status').textContent.includes('Тест подключения')`)) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(await window.webContents.executeJavaScript(`document.getElementById('status').textContent`), 'Тест подключения: повторите код.');
  const bottom = await window.webContents.executeJavaScript(`({bottom: document.getElementById('submit').getBoundingClientRect().bottom, height: innerHeight, overflow: document.body.scrollWidth > innerWidth})`);
  assert.ok(bottom.bottom < bottom.height); assert.equal(bottom.overflow, false);
  if (process.platform !== 'linux') {
    const vault = sessionVault(home, safeStorage);
    await vault.write({ access_token: 'va_qualification_secret' });
    assert.equal((await vault.read()).access_token, 'va_qualification_secret');
    assert.equal((await readFile(join(home, 'session.bin'))).includes(Buffer.from('qualification_secret')), false);
  }
  if (process.env.VELIA_QUALIFICATION_SCREENSHOT) {
    const screenshot = await window.webContents.capturePage();
    await writeFile(process.env.VELIA_QUALIFICATION_SCREENSHOT, screenshot.toPNG());
  }
  console.log('VELIA_UI_QUALIFIED', JSON.stringify({ platform: process.platform, isolation: true, pairingIPC: true,
    encryptedStorage: process.platform !== 'linux', controlsFit: true }));
} catch (error) { console.error(error.message); process.exitCode = 1; }
finally { clearTimeout(deadline); window?.destroy(); await rm(home, { recursive: true, force: true }); app.quit(); }
