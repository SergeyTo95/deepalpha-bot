import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { qualificationRuntime } from '../scripts/qualification-runtime.mjs';

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'velia-runner-test-'));
  await mkdir(join(root, 'notices'));
  await mkdir(join(root, 'node'));
  await writeFile(join(root, 'notices', 'BUILD.json'), JSON.stringify({ platform: 'win32', arch: process.arch }));
  return root;
}

for (const exitCode of [0, 23]) {
  test(`Wine transport captures regular-file output and preserves exit ${exitCode}`, { skip: process.platform !== 'linux' }, async () => {
    const root = await fixture();
    const old = process.env.VELIA_WINE;
    try {
      await symlink(process.execPath, join(root, 'node', 'node.exe'));
      const fakeWine = join(root, 'wine-transport.mjs');
      await writeFile(fakeWine, '#!/usr/bin/env node\n' +
        "import {spawnSync} from 'node:child_process'; const r=spawnSync(process.argv[2],process.argv.slice(3),{stdio:'inherit'});process.exit(r.status ?? 1);\n",
        { mode: 0o755 });
      process.env.VELIA_WINE = fakeWine;
      const runtime = qualificationRuntime(root);
      assert.equal(runtime.targetPath('/tmp/test file'), 'Z:\\tmp\\test file');
      const child = runtime.startNode(['-e', `const fs=require('fs'); if(!fs.fstatSync(1).isFile()||!fs.fstatSync(2).isFile())process.exit(99); console.log('stdout final'); console.error('stderr final'); process.exit(${exitCode});`]);
      let output = '', errors = '';
      child.stdout.on('data', chunk => { output += chunk; });
      child.stderr.on('data', chunk => { errors += chunk; });
      const [code] = await once(child, 'exit');
      assert.equal(code, exitCode);
      assert.match(output, /stdout final/); assert.match(errors, /stderr final/);
    } finally {
      if (old === undefined) delete process.env.VELIA_WINE; else process.env.VELIA_WINE = old;
      await rm(root, { recursive: true, force: true });
    }
  });
}

test('Qualification rejects a runtime architecture the host cannot execute', async () => {
  const root = await fixture();
  try {
    await writeFile(join(root, 'notices', 'BUILD.json'), JSON.stringify({ platform: process.platform, arch: 'incompatible' }));
    assert.throws(() => qualificationRuntime(root), /cannot execute/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
