import { app, BrowserWindow, dialog, shell, ipcMain, safeStorage } from 'electron';
import { spawn, execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, writeFile, rm, chmod } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { userInfo } from 'node:os';
import { promisify } from 'node:util';
import { defaultHome, gatewayURL, launchURL, profilePatch } from './config.mjs';
import { sessionManager } from './session.mjs';
import { sessionVault } from './vault.mjs';
import { pairDevice } from './pairing.mjs';
import { runtimePaths } from './runtime.mjs';
import { startProxy } from './proxy.mjs';
import { ownedPage } from './ipc.mjs';

const home = defaultHome();
const credentialPath = join(home, 'velia.credentials.json');
const gateway = gatewayURL(process.env.VELIA_GATEWAY_URL || 'https://deepalpha-ai.com/desktop-api/v1');
const uiRoot = resolve(import.meta.dirname, '..', 'ui');
app.setName('VELIA Desktop');
app.setPath('userData', join(home, 'electron'));
let child, window, proxy;
let stopping = false, quitReady = false;

function makeWindow(options = {}) {
  const result = new BrowserWindow({ title: 'VELIA Desktop', width: 1320, height: 900,
    backgroundColor: '#0b1020', ...options,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true,
      ...(options.webPreferences || {}) },
  });
  result.webContents.on('page-title-updated', event => { event.preventDefault(); result.setTitle('VELIA Desktop'); });
  result.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const external = new URL(url);
      if (external.protocol === 'https:' && !external.username && !external.password) void shell.openExternal(url);
    } catch { /* Malformed untrusted links remain closed. */ }
    return { action: 'deny' };
  });
  return result;
}

async function secureHome() {
  await mkdir(home, { recursive: true, mode: 0o700 });
  if (process.platform === 'win32') {
    await promisify(execFile)('icacls', [home, '/inheritance:r', '/grant:r', `${userInfo().username}:(OI)(CI)F`], { windowsHide: true });
  } else await chmod(home, 0o700);
}

async function connect(vault) {
  const page = join(uiRoot, 'connect.html');
  const pageURL = pathToFileURL(page).href;
  window = makeWindow({ width: 700, height: 800, minWidth: 520, minHeight: 650,
    webPreferences: { preload: join(import.meta.dirname, 'preload.cjs') },
  });
  const connectWindow = window;
  connectWindow.webContents.on('will-navigate', event => event.preventDefault());
  const trusted = event => ownedPage(event, connectWindow.webContents, pageURL);
  let pending = false;
  await new Promise((ok, fail) => {
    ipcMain.handle('velia:open-pairing', async event => {
      if (!trusted(event)) throw new Error('Untrusted sender');
      await shell.openExternal(new URL(gateway).origin + '/mobile-connect');
    });
    ipcMain.handle('velia:pair', async (event, code) => {
      if (!trusted(event)) throw new Error('Untrusted sender');
      if (pending) return { ok: false, error: 'Подключение уже выполняется.' };
      pending = true;
      try {
        await vault.write(await pairDevice(gateway, code));
        ok(); return { ok: true };
      } catch (error) { return { ok: false, error: error.message }; }
      finally { pending = false; }
    });
    connectWindow.once('closed', () => fail(new Error('Подключение отменено.')));
    void connectWindow.loadFile(page).catch(fail);
  }).finally(() => {
    ipcMain.removeHandler('velia:open-pairing'); ipcMain.removeHandler('velia:pair');
  });
}

async function start() {
  await secureHome();
  const paths = runtimePaths({ packaged: app.isPackaged, resources: process.resourcesPath,
    desktopRoot: resolve(import.meta.dirname, '..'), platform: process.platform,
    developmentNode: process.env.VELIA_NODE_PATH || 'node' });
  if (!existsSync(paths.bin) || (app.isPackaged && !existsSync(paths.node))) {
    throw new Error(app.isPackaged ? 'Установка Велии повреждена. Установите приложение заново.' : 'Сначала выполните npm run build:harness в каталоге desktop.');
  }
  const vault = sessionVault(home, safeStorage);
  await vault.migrate();
  try { await vault.read(); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    await connect(vault);
  }
  const selection = await dialog.showOpenDialog(window, { title: 'VELIA — выберите рабочую папку', properties: ['openDirectory'] });
  if (selection.canceled) { app.quit(); return; }
  const renew = sessionManager(vault);
  const session = await renew();
  if (new URL(gateway).origin !== new URL(session.origin).origin) throw new Error('Адрес шлюза не совпадает с аккаунтом.');
  proxy = await startProxy(gateway, renew);
  await writeFile(credentialPath, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: proxy.key }, records: {} }), { mode: 0o600 });
  const patch = join(home, 'velia.patch.json');
  await writeFile(patch, JSON.stringify(profilePatch(gateway, credentialPath, proxy.url), null, 2), { mode: 0o600 });
  child = spawn(paths.node, [paths.bin, '--profile', 'web', '--patch', patch, '--host', '127.0.0.1', '--port', '0', '--no-open'], {
    cwd: selection.filePaths[0], windowsHide: true, detached: process.platform !== 'win32',
    env: { ...process.env, DSH_HOME: home, DSH_PERMISSION_MODE: 'workspace-write' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let buffer = '';
  const connectingWindow = window;
  await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error('Локальный движок не запустился за 60 секунд.')), 60000);
    let launched = false;
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', () => { clearTimeout(timer); fail(new Error('Локальный движок остановился до запуска интерфейса.')); });
    child.stderr.resume();
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      if (buffer.length > 65536) { clearTimeout(timer); fail(new Error('Некорректный вывод локального движка.')); return; }
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        let url;
        try { url = launchURL(line); } catch (error) { clearTimeout(timer); fail(error); return; }
        if (!url || launched) continue;
        launched = true; clearTimeout(timer);
        window = makeWindow();
        const localOrigin = new URL(url).origin;
        window.webContents.on('will-navigate', (event, target) => {
          try { if (new URL(target).origin !== localOrigin) event.preventDefault(); }
          catch { event.preventDefault(); }
        });
        window.loadURL(url).then(() => { connectingWindow?.close(); ok(); }, fail);
      }
    });
  });
  child.on('exit', () => {
    if (!stopping) { dialog.showErrorBox('VELIA Desktop', 'Локальный движок остановился. Перезапустите приложение.'); app.quit(); }
  });
}

async function shutdown() {
  stopping = true;
  try {
    if (child?.pid && child.exitCode === null) {
      if (process.platform === 'win32') await promisify(execFile)('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true });
      else {
        process.kill(-child.pid, 'SIGTERM');
        await new Promise(resolvePromise => { child.once('exit', resolvePromise); setTimeout(resolvePromise, 4000); });
        if (child.exitCode === null && child.signalCode === null) process.kill(-child.pid, 'SIGKILL');
      }
    }
  } catch { /* A child that already exited needs no further termination. */ }
  await proxy?.close();
  await rm(credentialPath, { force: true });
}
app.on('before-quit', event => {
  if (quitReady) return;
  event.preventDefault();
  if (!stopping) void shutdown().finally(() => { quitReady = true; app.quit(); });
});
app.on('window-all-closed', () => app.quit());
if (!app.requestSingleInstanceLock()) { quitReady = true; app.quit(); }
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.whenReady().then(start).catch(error => {
    if (!stopping) { dialog.showErrorBox('VELIA Desktop', error.message); app.quit(); }
  });
}
