import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshSession, sessionManager } from '../src/session.mjs';
import { pairDevice } from '../src/pairing.mjs';

const oldSession = () => ({ origin: 'https://api.example', device_id: 'device', refresh_token: 'vr_old', expiresAt: 0 });
const rotated = () => ({ ok: true, access_token: 'va_new', refresh_token: 'vr_rotated', access_expires_in: 300 });

test('refresh persists rotated tokens with backend lifetime in the encrypted store', async () => {
  let saved = oldSession();
  const storage = { read: async () => saved, write: async value => { saved = value; } };
  const start = Date.now();
  const session = await refreshSession(storage, async (url, options) => {
    assert.equal(url, 'https://api.example/mobile-api/v1/auth/refresh');
    assert.equal(JSON.parse(options.body).refresh_token, 'vr_old');
    assert.equal(options.redirect, 'error');
    return { ok: true, json: async () => rotated() };
  });
  assert.equal(session.refresh_token, 'vr_rotated');
  assert.ok(session.expiresAt >= start + 300000 && session.expiresAt < start + 305000);
  await refreshSession(storage, () => { throw new Error('unnecessary refresh'); });
});

test('failed or malformed refresh keeps the previous session for retry', async () => {
  for (const value of [{ ok: false }, { ...rotated(), access_expires_in: '300' }]) {
    let saved = oldSession();
    const storage = { read: async () => saved, write: async value => { saved = value; } };
    await assert.rejects(refreshSession(storage, async () => ({ ok: true, json: async () => value })));
    assert.equal(saved.refresh_token, 'vr_old');
  }
});

test('simultaneous model requests rotate the session exactly once', async () => {
  let requests = 0;
  const storage = { read: async () => oldSession(), write: async () => {} };
  const renew = sessionManager(storage, async () => {
    requests++; await new Promise(resolve => setTimeout(resolve, 10));
    return { ok: true, json: async () => rotated() };
  });
  const sessions = await Promise.all([renew(), renew(), renew()]);
  assert.equal(requests, 1);
  assert.equal(sessions[0], sessions[2]);
});

test('pairing accepts the displayed code and keeps origin/device locally owned', async () => {
  const session = await pairDevice('https://api.example/desktop-api/v1', 'abcd efgh.jklm_npqr', async (url, options) => {
    assert.equal(url, 'https://api.example/mobile-api/v1/auth/exchange');
    assert.equal(JSON.parse(options.body).pairing_code, 'ABCDEFGHJKLMNPQR');
    assert.equal(options.redirect, 'error');
    return { ok: true, json: async () => ({ ...rotated(), origin: 'https://other.example', device_id: 'wrong' }) };
  });
  assert.equal(session.origin, 'https://api.example');
  assert.notEqual(session.device_id, 'wrong');
});

test('invalid pairing codes never make network requests', async () => {
  for (const code of ['short', 'ABCD-EFGH-JKLM-NPQ0', 'ABCD-EFGH-JKLM-NPQR-extra']) {
    await assert.rejects(pairDevice('https://api.example/v1', code, () => { throw new Error('unexpected network'); }), /16/);
  }
});
