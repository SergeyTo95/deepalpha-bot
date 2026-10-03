import { join } from 'node:path';

export function runtimePaths({ packaged, resources, desktopRoot, platform, developmentNode = 'node' }) {
  return packaged ? {
    node: join(resources, 'node', platform === 'win32' ? 'node.exe' : 'node'),
    bin: join(resources, 'harness', 'lib', 'bin.js'),
  } : {
    node: developmentNode,
    bin: join(desktopRoot, '.runtime', 'harness', 'apps', 'cli', 'lib', 'bin.js'),
  };
}
