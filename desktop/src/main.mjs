import { app, BrowserWindow, dialog, shell } from 'electron';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { defaultHome, launchURL, profilePatch } from './config.mjs';
import { refreshSession } from './session.mjs';

app.setName('VELIA Desktop');
app.setPath('userData', join(defaultHome(), 'electron'));
let child;
let window;
let stopping = false;
let refreshTimer;

async function start() {
  const checkout = resolve(import.meta.dirname, '..', '.runtime', 'harness');
  const bin = join(checkout, 'apps', 'cli', 'lib', 'bin.js');
  if (!existsSync(bin)) throw new Error('Сначала выполните npm run build:harness в каталоге desktop.');
  const selection = await dialog.showOpenDialog({ title: 'VELIA — выберите рабочую папку', properties: ['openDirectory'] });
  if (selection.canceled) { app.quit(); return; }
  const home = defaultHome();
  await mkdir(home, { recursive: true });
  const credentialPath = join(home, 'velia.credentials.json');
  const gateway = process.env.VELIA_GATEWAY_URL || 'https://deepalpha-ai.com/desktop-api/v1';
  const renew = async () => {
    const session = await refreshSession(home);
    if (new URL(gateway).origin !== new URL(session.origin).origin) throw new Error('Адрес шлюза не совпадает с адресом подключённого аккаунта.');
    await writeFile(credentialPath, JSON.stringify({ version: 1, refs: { VELIA_ACCESS_TOKEN: session.access_token }, records: {} }), { mode: 0o600 });
  };
  await renew();
  refreshTimer = setInterval(() => { void renew().catch(() => {
    clearInterval(refreshTimer);
    dialog.showErrorBox('VELIA Desktop', 'Сессия истекла. Подключите устройство заново командой npm run connect и перезапустите приложение.');
    app.quit();
  }); }, 45000);
  const patch = join(home, 'velia.patch.json');
  await writeFile(patch, JSON.stringify(profilePatch(gateway, credentialPath), null, 2), { mode: 0o600 });
  child = spawn(process.env.VELIA_NODE_PATH || 'node', [bin, '--profile', 'web', '--patch', patch, '--host', '127.0.0.1', '--port', '0', '--no-open'], {
    cwd: selection.filePaths[0], windowsHide: true,
    env: { ...process.env, DSH_HOME: home, DSH_PERMISSION_MODE: 'workspace-write' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let buffer = '';
  await new Promise((ok, fail) => {
    const timer = setTimeout(() => fail(new Error('Не удалось запустить локальный движок Велии за 60 секунд.')), 60000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', () => { clearTimeout(timer); fail(new Error('Локальный движок Велии завершился до запуска интерфейса.')); });
    child.stderr.resume(); // Do not expose logs containing local launch credentials.
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      const lines = buffer.split('\n'); buffer = lines.pop();
      for (const line of lines) {
        let url;
        try { url = launchURL(line); } catch (error) { clearTimeout(timer); fail(error); return; }
        if (!url || window) continue;
        clearTimeout(timer);
        window = new BrowserWindow({ title: 'VELIA Desktop', width: 1320, height: 900,
          backgroundColor: '#0c1020', webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
        });
        const localOrigin = new URL(url).origin;
        window.webContents.on('page-title-updated', event => { event.preventDefault(); window.setTitle('VELIA Desktop'); });
        window.webContents.on('will-navigate', (event, target) => { if (new URL(target).origin !== localOrigin) event.preventDefault(); });
        window.webContents.setWindowOpenHandler(({ url: target }) => {
          const external = new URL(target);
          if (external.protocol === 'https:' && !external.username && !external.password) void shell.openExternal(target);
          return { action: 'deny' };
        });
        window.loadURL(url).then(ok, fail);
      }
    });
  });
  child.on('exit', () => { if (!stopping) { dialog.showErrorBox('VELIA Desktop', 'Локальный движок остановился. Перезапустите приложение.'); app.quit(); } });
}
app.on('before-quit', () => {
  stopping = true; clearInterval(refreshTimer);
  if (child?.pid && process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  else child?.kill();
});
app.on('window-all-closed', () => app.quit());
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { window?.show(); window?.focus(); });
  app.whenReady().then(start).catch(error => { dialog.showErrorBox('VELIA Desktop', error.message); app.quit(); });
}
