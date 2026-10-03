import assert from 'node:assert/strict';
import { join, resolve } from 'node:path';
import { qualificationRuntime } from './qualification-runtime.mjs';

const root = resolve(import.meta.dirname, '..');
const runtime = qualificationRuntime(process.env.VELIA_QUALIFICATION_RESOURCES || join(root, '.runtime', 'package'));
assert.equal(runtime.platform, 'win32');
const probe = `
const assert = require('node:assert/strict');
const fs = require('node:fs');
const requireRuntime = require('node:module').createRequire(${JSON.stringify(runtime.targetPath(runtime.bin))});
assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
assert.equal(typeof requireRuntime('node-pty').spawn, 'function');
const conpty = requireRuntime('node-pty/lib/utils.js').loadNativeModule('conpty').module;
assert.equal(typeof conpty.startProcess, 'function');
const koffi = requireRuntime('koffi');
const kernel = koffi.load('kernel32.dll');
assert.equal(kernel.func('uint32_t __stdcall GetCurrentProcessId(void)')(), process.pid);
kernel.unload();
const native = Object.keys(require.cache).filter(path => path.endsWith('.node'));
assert.ok(native.some(path => path.includes('conpty')));
assert.ok(native.some(path => path.includes('koffi')));
for (const path of native) {
  const bytes = fs.readFileSync(path), offset = bytes.readUInt32LE(0x3c);
  assert.equal(bytes.toString('ascii', 0, 2), 'MZ');
  assert.equal(bytes.readUInt32LE(offset), 0x4550);
  assert.equal(bytes.readUInt16LE(offset + 4), 0x8664);
}
console.log('VELIA_WINDOWS_DEPS_QUALIFIED ' + JSON.stringify({platform:process.platform,arch:process.arch,node:process.version,native:native.length}));
`;
const child = runtime.startNode(['-e', probe]);
let output = '', errors = '';
child.stdout.on('data', chunk => { output += chunk; });
child.stderr.on('data', chunk => { errors += chunk; });
await new Promise((ok, fail) => {
  const timer = setTimeout(() => { child.kill('SIGKILL'); fail(new Error('Windows native dependency probe timed out')); }, 30000);
  child.once('error', error => { clearTimeout(timer); fail(error); });
  child.once('exit', code => { clearTimeout(timer); if (code !== 0) fail(new Error(errors)); else ok(); });
});
assert.match(output, /VELIA_WINDOWS_DEPS_QUALIFIED/);
process.stdout.write(output);
