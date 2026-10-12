/** Encrypted device cache. Keys exist only in memory after server authentication.
 * This protects stored bytes, not an unlocked session or compromised origin.
 */
export class LocalHistory {
  constructor(store, cryptoAPI = globalThis.crypto) {
    this.store = store; this.crypto = cryptoAPI; this.epoch = 0; this.tail = Promise.resolve();
  }
  lock() { this.epoch++; this.account = null; this.key = null; }
  async unlock(account, encodedKey) {
    this.lock(); const epoch = this.epoch;
    if (!/^[a-f0-9]{24}$/.test(account) || !/^[a-f0-9]{64}$/.test(encodedKey || '')) throw new Error('Invalid cache credentials');
    const bytes = Uint8Array.from(encodedKey.match(/../g), x => parseInt(x, 16));
    const key = await this.crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
    bytes.fill(0);
    if (epoch !== this.epoch) return false;
    this.account = account; this.key = key; return true;
  }
  async read() {
    const {account, key, epoch} = this;
    if (!key) return null;
    const record = await this.store.get(account);
    if (!record) return null;
    const bytes = await this.crypto.subtle.decrypt({name:'AES-GCM', iv:record.iv,
      additionalData:new TextEncoder().encode('velia-history-v1:' + account)}, key, record.data);
    if (epoch !== this.epoch) return null;
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  write(value) {
    const {account, key, epoch} = this;
    if (!key) return Promise.resolve(false);
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    if (bytes.length > 12 * 1024 * 1024) return Promise.reject(new Error('Cache limit exceeded'));
    const job = async () => {
      if (epoch !== this.epoch) return false;
      const iv = this.crypto.getRandomValues(new Uint8Array(12));
      const data = await this.crypto.subtle.encrypt({name:'AES-GCM', iv,
        additionalData:new TextEncoder().encode('velia-history-v1:' + account)}, key, bytes);
      if (epoch !== this.epoch) return false;
      await this.store.put(account, {iv, data, version:1}); return true;
    };
    const pending = this.tail.catch(() => {}).then(job); this.tail = pending; return pending;
  }
  async clear() {
    const account = this.account; this.lock();
    await this.tail.catch(() => {});
    if (account) await this.store.delete(account);
  }
}
export function browserHistoryStore(indexedDBAPI = globalThis.indexedDB, timeoutMs = 5000) {
  let database;
  const open = () => {
    if (database) return database;
    database = new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => { settled = true; reject(new Error('History storage timed out')); }, timeoutMs);
      let request;
      try { request = indexedDBAPI.open('velia-encrypted-history-v1', 1); }
      catch (error) { clearTimeout(timer); settled = true; reject(error); return; }
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains('accounts')) request.result.createObjectStore('accounts');
      };
      request.onsuccess = () => {
        clearTimeout(timer);
        if (settled) { request.result.close(); return; }
        settled = true;
        const db = request.result;
        db.onversionchange = () => { db.close(); database = null; };
        resolve(db);
      };
      request.onerror = () => { clearTimeout(timer); settled = true; reject(request.error); };
    }).catch(error => { database = null; throw error; });
    return database;
  };
  const run = async (mode, action) => {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('accounts', mode), req = action(tx.objectStore('accounts'));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = tx.onabort = () => reject(tx.error || new Error('History storage unavailable'));
    });
  };
  return {get:key=>run('readonly', s=>s.get(key)), put:(key,value)=>run('readwrite', s=>s.put(value,key)),
    delete:key=>run('readwrite', s=>s.delete(key))};
}
