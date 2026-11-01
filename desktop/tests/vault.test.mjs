import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { sessionVault } from '../src/vault.mjs';

function encryption() {
  const key = randomBytes(32); const iv = randomBytes(16);
  return { isEncryptionAvailable: () => true,
    encryptString: value => { const cipher = createCipheriv('aes-256-cbc', key, iv); return Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]); },
    decryptString: value => { const cipher = createDecipheriv('aes-256-cbc', key, iv); return Buffer.concat([cipher.update(value), cipher.final()]).toString('utf8'); },
  };
}
test('session is encrypted and the legacy plaintext file is removed after migration', async () => {
  const home = await mkdtemp(join(tmpdir(), 'velia-vault-'));
  try {
    const session = { access_token: 'va_secret', refresh_token: 'vr_secret', origin: 'https://api.example' };
    await writeFile(join(home, 'session.json'), JSON.stringify(session));
    const vault = sessionVault(home, encryption()); await vault.migrate();
    assert.deepEqual(await vault.read(), session);
    assert.equal((await readFile(join(home, 'session.bin'))).includes(Buffer.from('secret')), false);
    await assert.rejects(stat(join(home, 'session.json')), { code: 'ENOENT' });
    if (process.platform !== 'win32') assert.equal((await stat(join(home, 'session.bin'))).mode & 0o777, 0o600);
  } finally { await rm(home, { recursive: true, force: true }); }
});
test('unavailable encryption and Linux plaintext fallback refuse writes', async () => {
  const home = await mkdtemp(join(tmpdir(), 'velia-vault-'));
  try {
    for (const backend of [ { isEncryptionAvailable: () => false },
      { isEncryptionAvailable: () => true, getSelectedStorageBackend: () => 'basic_text' } ]) {
      await assert.rejects(sessionVault(home, backend).write({ access_token: 'va_secret' }), /хранилище/);
    }
    await assert.rejects(stat(join(home, 'session.bin')), { code: 'ENOENT' });
  } finally { await rm(home, { recursive: true, force: true }); }
});
