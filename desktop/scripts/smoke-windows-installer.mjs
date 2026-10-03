import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdtemp, readFile, readdir, rm, stat } from 'node:fs/promises';
import { resolve, join, relative } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const require = createRequire(import.meta.url);
const { getWineToolset } = require('app-builder-lib/out/toolsets/wine.js');
const { execPath: wine, env: wineEnv } = await getWineToolset('1.0.1');
const env = { ...process.env, ...wineEnv, USE_SYSTEM_WINE: 'false' };
const winPath = path => `Z:${resolve(path).replaceAll('/', '\\')}`;
function run(command, args, extraEnv = {}, timeout = 180_000) {
  const child = spawnSync(command, args, { cwd: root, stdio: 'inherit',
    env: { ...env, ...extraEnv }, timeout });
  if (child.error || child.status !== 0) throw child.error || new Error(`Installer qualification failed: ${command} (${child.status})`);
}
const installers = (await readdir(join(root, 'dist')))
  .filter(name => /^VELIA-Desktop-[A-Za-z0-9.-]+-win-x64\.exe$/.test(name));
assert.equal(installers.length, 1, 'Exactly one final installer must exist');
const staging = await mkdtemp(join(root, 'dist', '.installed-'));
const installed = join(staging, 'app');
try {
  run(wine, ['winecfg', '-v', 'win10']);
  run(wine, [winPath(join(root, 'dist', installers[0])), '/S', `/D=${winPath(installed)}`],
    { __COMPAT_LAYER: 'RunAsInvoker' });
  let files = 0;
  const unpacked = join(root, 'dist', 'win-unpacked');
  async function check(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const expected = join(directory, entry.name);
      const actual = join(installed, relative(unpacked, expected));
      if (entry.isDirectory()) {
        assert.ok((await stat(actual)).isDirectory(), `Missing installed directory: ${relative(unpacked, expected)}`);
        await check(expected);
      } else if (entry.isSymbolicLink()) {
        // Windows installs may materialize an internal deployment link as a real file/directory.
        assert.equal((await stat(actual)).isDirectory(), (await stat(expected)).isDirectory());
      } else {
        const expectedHash = createHash('sha256').update(await readFile(expected)).digest('hex');
        const actualHash = createHash('sha256').update(await readFile(actual)).digest('hex');
        assert.equal(actualHash, expectedHash, `Changed installed file: ${relative(unpacked, expected)}`);
        files++;
      }
    }
  }
  await check(unpacked);
  const resources = join(installed, 'resources');
  const qualificationEnv = { VELIA_WINE: wine, VELIA_QUALIFICATION_RESOURCES: resources };
  for (const script of ['smoke-windows-deps.mjs', 'smoke-harness.mjs', 'smoke-web.mjs']) {
    run(process.execPath, [join(root, 'scripts', script)], qualificationEnv);
  }
  console.log('VELIA_WINDOWS_INSTALLER_QUALIFIED', JSON.stringify({
    installedUnderWine: true, verifiedFiles: files, installedRuntime: true, installedWeb: true,
  }));
} finally {
  await rm(staging, { recursive: true, force: true });
}
