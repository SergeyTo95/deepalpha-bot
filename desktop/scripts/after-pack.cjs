const { cp } = require('node:fs/promises');
const { join } = require('node:path');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { readdir, lstat, realpath } = require('node:fs/promises');
const { relative, isAbsolute, sep } = require('node:path');
const { readFile } = require('node:fs/promises');
const { pathToFileURL } = require('node:url');

async function checkLinks(root, directory = root) {
  for (const name of await readdir(directory)) {
    const path = join(directory, name), stat = await lstat(path);
    if (stat.isSymbolicLink()) {
      const target = relative(root, await realpath(path));
      if (isAbsolute(target) || target === '..' || target.startsWith('..' + sep)) {
        throw new Error('Packaged runtime contains a link outside its own directory');
      }
    } else if (stat.isDirectory()) await checkLinks(root, path);
  }
}

// Resource matching skips nested node_modules; copy the complete deployed tree.
module.exports = async context => {
  const resources = context.packager.getResourcesDir(context.appOutDir);
  await cp(join(context.packager.projectDir, '.runtime', 'package', 'harness'), join(resources, 'harness'),
    { recursive: true, verbatimSymlinks: true });
  await checkLinks(join(resources, 'harness'));
  const build = JSON.parse(await readFile(join(resources, 'notices', 'BUILD.json'), 'utf8'));
  if (build.platform !== context.packager.platform.nodeName) throw new Error('Runtime and installer target differ');
  const { qualificationRuntime, runtimeProbe } = await import(pathToFileURL(join(context.packager.projectDir,
    'scripts', 'qualification-runtime.mjs')).href);
  await runtimeProbe(qualificationRuntime(resources));
  const scripts = ['smoke-harness.mjs', 'smoke-web.mjs'];
  if (build.platform === 'win32') scripts.unshift('smoke-windows-deps.mjs');
  for (const script of scripts) {
    const result = await promisify(execFile)(process.execPath, [join(context.packager.projectDir, 'scripts', script)],
      { timeout: 60000, env: { ...process.env, VELIA_QUALIFICATION_RESOURCES: resources } });
    process.stdout.write(result.stdout);
  }
};
