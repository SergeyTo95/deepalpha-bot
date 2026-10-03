import { readFileSync, mkdtempSync, openSync, closeSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { spawn } from 'node:child_process';
import { PassThrough } from 'node:stream';
import { runtimePaths } from '../src/runtime.mjs';

/** Run a packaged target runtime natively, or Windows x64 under Wine on Linux. */
export function qualificationRuntime(resources) {
  resources = resolve(resources);
  const build = JSON.parse(readFileSync(join(resources, 'notices', 'BUILD.json'), 'utf8'));
  const wine = build.platform === 'win32' && process.platform === 'linux';
  if ((!wine && build.platform !== process.platform) || build.arch !== process.arch) {
    throw new Error('Qualification host cannot execute this runtime target');
  }
  const paths = runtimePaths({ packaged: true, resources, platform: build.platform });
  const targetPath = path => wine ? 'Z:' + resolve(path).replaceAll('/', '\\') : path;
  function startNode(args, options = {}) {
    if (!wine) return spawn(paths.node, args, { ...options, stdio: ['ignore', 'pipe', 'pipe'] });
    // Windows Node under Wine cannot use the host's pipe descriptors (EBADF).
    const logs = mkdtempSync(join(tmpdir(), 'velia-wine-'));
    const files = [join(logs, 'stdout.log'), join(logs, 'stderr.log')];
    const descriptors = files.map(file => openSync(file, 'w'));
    let child;
    try { child = spawn(process.env.VELIA_WINE || 'wine', [paths.node, ...args],
      { ...options, stdio: ['ignore', ...descriptors] }); }
    finally { descriptors.forEach(closeSync); }
    const streams = [new PassThrough(), new PassThrough()], offsets = [0, 0];
    child.stdout = streams[0]; child.stderr = streams[1];
    function pump() {
      files.forEach((file, index) => {
        const bytes = readFileSync(file);
        if (bytes.length > offsets[index]) streams[index].write(bytes.subarray(offsets[index]));
        offsets[index] = bytes.length;
      });
    }
    const timer = setInterval(pump, 100);
    let finished = false;
    function finish() {
      if (finished) return;
      finished = true;
      clearInterval(timer); pump(); streams.forEach(stream => stream.end());
      rmSync(logs, { recursive: true, force: true });
    }
    child.once('exit', finish); child.once('error', finish);
    return child;
  }
  return { ...paths, platform: build.platform, wine, targetPath, startNode,
    start: (args, options) => startNode([targetPath(paths.bin), ...args], options) };
}

export async function runtimeProbe(runtime) {
  const child = runtime.start(['--version']);
  let output = '', errors = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { errors += chunk; });
  await new Promise((ok, fail) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); fail(new Error('Runtime version probe timed out')); }, 30000);
    child.once('error', error => { clearTimeout(timer); fail(error); });
    child.once('exit', code => {
      clearTimeout(timer);
      if (code !== 0) fail(new Error('Runtime version probe failed: ' + errors)); else ok();
    });
  });
  if (!output.trim()) throw new Error('Runtime version probe returned no version');
  return output.trim();
}
