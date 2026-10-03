import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { saveSession, refreshSession } from '../src/session.mjs';

test('refresh persists the rotated token with actual backend expiry', async () => {
  const home = await mkdtemp(join(tmpdir(), 'velia-session-'));
  try {
    await saveSession(home, { origin: 'https://api.example', device_id: 'device', refresh_token: 'old', expiresAt: 0 });
    const start = Date.now();
    const session = await refreshSession(home, async (url, options) => {
      assert.equal(url, 'https://api.example/mobile-api/v1/auth/refresh');
      assert.equal(JSON.parse(options.body).refresh_token, 'old');
      return { ok: true, json: async () => ({ ok: true, access_token: 'new-access', refresh_token: 'rotated', access_expires_in: 300 }) };
    });
    assert.equal(session.refresh_token, 'rotated');
    assert.ok(session.expiresAt >= start + 300000);
    assert.ok(session.expiresAt < start + 305000);
    assert.equal(JSON.parse(await readFile(join(home, 'session.json'), 'utf8')).refresh_token, 'rotated');
    await refreshSession(home, () => { throw new Error('unnecessary refresh'); });
  } finally { await rm(home, { recursive: true, force: true }); }
});
test('failed refresh preserves the existing session for retry', async () => {
  const home = await mkdtemp(join(tmpdir(), 'velia-session-'));
  try {
    await saveSession(home, { origin: 'https://api.example', refresh_token: 'old', expiresAt: 0 });
    await assert.rejects(refreshSession(home, async () => ({ ok: false, json: async () => ({ ok: false }) })));
    assert.equal(JSON.parse(await readFile(join(home, 'session.json'), 'utf8')).refresh_token, 'old');
  } finally { await rm(home, { recursive: true, force: true }); }
});
