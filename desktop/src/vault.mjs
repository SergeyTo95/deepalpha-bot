import { readFile, writeFile, rename, rm } from 'node:fs/promises';
import { join } from 'node:path';

/** Store only encrypted device sessions; refuse Electron's plaintext fallback. */
export function sessionVault(home, encryption) {
  const path = join(home, 'session.bin');
  function available() {
    if (!encryption.isEncryptionAvailable() || encryption.getSelectedStorageBackend?.() === 'basic_text') {
      throw new Error('Защищённое хранилище ОС недоступно. Разблокируйте связку ключей и перезапустите Велию.');
    }
  }
  return {
    available,
    async read() { available(); return JSON.parse(encryption.decryptString(await readFile(path))); },
    async write(session) {
      available();
      await writeFile(path + '.tmp', encryption.encryptString(JSON.stringify(session)), { mode: 0o600 });
      await rename(path + '.tmp', path);
    },
    async migrate() {
      available();
      try { await this.read(); } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        try { await this.write(JSON.parse(await readFile(join(home, 'session.json'), 'utf8'))); }
        catch (legacyError) { if (legacyError.code !== 'ENOENT') throw legacyError; }
      }
      await rm(join(home, 'session.json'), { force: true });
      await rm(join(home, 'session.json.tmp'), { force: true });
    },
  };
}
