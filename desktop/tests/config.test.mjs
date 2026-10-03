import test from 'node:test';
import assert from 'node:assert/strict';
import { gatewayURL, launchURL, profilePatch } from '../src/config.mjs';

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
  assert.equal(patch.find(p => p.id === 'product-analytics').disabled, true);
  assert.equal(patch.find(p => p.id === 'desktop-product-telemetry').disabled, true);
});
