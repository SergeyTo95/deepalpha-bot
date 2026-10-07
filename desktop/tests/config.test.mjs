import test from 'node:test';
import assert from 'node:assert/strict';
import { browserAgentPatch, gatewayURL, launchURL, profilePatch, defaultHome } from '../src/config.mjs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';

test('preview data can be isolated without modifying the existing account store', () => {
  assert.equal(defaultHome(''), join(homedir(), '.velia-desktop'));
  assert.equal(defaultHome(join(tmpdir(), 'velia-preview')), join(tmpdir(), 'velia-preview'));
  assert.throws(() => defaultHome('relative-preview'));
});

test('gateway refuses credentials and insecure URLs', () => {
  for (const url of ['http://api.example/v1', 'https://user:secret@api.example/v1', 'https://api.example/v1?key=secret']) {
    assert.throws(() => gatewayURL(url));
  }
  assert.equal(gatewayURL('https://api.example/v1/'), 'https://api.example/v1');
});
test('launch accepts only the child loopback port', () => {
  assert.equal(launchURL('dsh web: http://127.0.0.1:9876/?token=abc', 9876), 'http://127.0.0.1:9876/?token=abc');
  assert.throws(() => launchURL('dsh web: http://attacker.example:9876/', 9876));
  assert.throws(() => launchURL('dsh web: http://127.0.0.1:9877/', 9876));
  assert.equal(launchURL('booting', 9876), null);
  assert.equal(launchURL('dsh web: http://127.0.0.1:54321/?token=abc'), 'http://127.0.0.1:54321/?token=abc');
  assert.throws(() => launchURL('dsh web: http://127.0.0.1/'));
});
test('profile uses a credential reference and turns analytics off', () => {
  const patch = profilePatch('https://api.example/v1');
  assert.equal(patch[0].config.providers.velia.apiKeyEnv, 'VELIA_ACCESS_TOKEN');
  assert.deepEqual(patch[0].config.providers.velia.models.map(m => m.id), ['velia-pro', 'velia-flash']);
  assert.equal(patch[0].config.providers.velia.models[1].contextWindow, 8192);
  assert.equal(patch[0].config.providers.velia.models[1].maxTokens, 512);
  assert.equal(patch.find(p => p.id === 'product-analytics').disabled, true);
  assert.equal(patch.find(p => p.id === 'desktop-product-telemetry').disabled, true);
});
test('local provider exception accepts only the owned loopback gateway', () => {
  const patch = profilePatch('https://api.example/v1', '/credentials', 'http://127.0.0.1:43210/v1');
  assert.equal(patch.find(p => p.id === 'llm-pi-ai').config.providers.velia.baseURL, 'http://127.0.0.1:43210/v1');
  for (const url of ['http://attacker.example:43210/v1', 'http://localhost:43210/v1', 'http://127.0.0.1/v1']) {
    assert.throws(() => profilePatch('https://api.example/v1', '/credentials', url));
  }
});


test('browser agent is Flash-only and exposes browser capabilities without host tools', () => {
  const patch = browserAgentPatch('https://api.example/v1', '/credentials', 'http://127.0.0.1:43210/v1', '/usr/bin/chromium');
  const provider = patch.find(p => p.id === 'llm-pi-ai').config.providers.velia;
  assert.deepEqual(provider.models.map(model => model.id), ['velia-flash']);
  assert.equal(patch.find(p => p.id === 'agent-default-model').config.model, 'velia-flash');
  assert.equal(patch.find(p => p.id === 'agent-preset-registry').config.default, 'velia-browser');
  for (const id of ['preset-standard', 'preset-ptc', 'preset-minimal', 'preset-cordis',
    'ui-agent-preset', 'ui-plugin-manager', 'ui-sidebar-terminal', 'ui-sidebar-files',
    'terminal-controller', 'workspace-files', 'workspace-controller', 'directory-picker']) {
    assert.equal(patch.find(p => p.id === id).disabled, true, id);
  }
  const inserted = patch.find(p => Array.isArray(p.insert)).insert;
  assert.ok(inserted.some(p => p.name === '@deepseek-ai/dsh-browser-use'));
  const browser = inserted.find(p => p.name === '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp');
  assert.deepEqual(browser.config, { mode: 'launch', headless: true,
    executablePath: '/usr/bin/chromium', toolCallTimeoutMs: 45000 });
  const preset = inserted.find(p => p.id === 'preset-velia-browser');
  assert.equal(preset.config.plugins.some(p => /tool-(bash|pwsh|fs)/.test(p.id)), false);
  assert.throws(() => browserAgentPatch('https://api.example/v1', undefined, undefined, 'chromium'));
});
