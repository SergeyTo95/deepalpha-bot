import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {LocalHistory, browserHistoryStore} from '../local-history.mjs';
const account = 'a'.repeat(24), key = '1'.repeat(64);
function fixture() {
  const records = new Map();
  const store = {get:async id=>records.get(id),put:async(id,v)=>records.set(id,v),delete:async id=>records.delete(id)};
  return {records, store, cache:new LocalHistory(store,webcrypto)};
}
test('disk holds ciphertext; a new authenticated session can read it', async()=>{
  const {cache,store,records}=fixture(); await cache.unlock(account,key);
  const value={chats:[{title:'private medical message'}],active:'private-id'};
  await cache.write(value);
  assert.ok(!JSON.stringify(records.get(account)).includes('private'));
  assert.ok(!new TextDecoder().decode(records.get(account).data).includes('private'));
  cache.lock(); assert.equal(await cache.read(),null); assert.equal(await cache.write(value),false);
  const next=new LocalHistory(store,webcrypto); await next.unlock(account,key);
  assert.deepEqual(await next.read(),value);
});
test('account swaps and wrong keys cannot decrypt another account',async()=>{
  const {cache,store,records}=fixture();await cache.unlock(account,key);await cache.write({secret:'A'});
  await cache.unlock('b'.repeat(24),'2'.repeat(64));assert.equal(await cache.read(),null);
  records.set('b'.repeat(24),records.get(account));await assert.rejects(cache.read());
  const wrong=new LocalHistory(store,webcrypto);await wrong.unlock(account,'2'.repeat(64));await assert.rejects(wrong.read());
});
test('tampering is rejected and each save uses a fresh nonce',async()=>{
  const {cache,records}=fixture();await cache.unlock(account,key);await cache.write({secret:'x'});
  const first=records.get(account);await cache.write({secret:'x'});assert.notDeepEqual(first.iv,records.get(account).iv);
  const record=records.get(account);new Uint8Array(record.data)[0]^=1;await assert.rejects(cache.read());
});
test('queued saves cannot resurrect data after logout',async()=>{
  const {cache,records}=fixture();await cache.unlock(account,key);
  const pending=cache.write({secret:'old account'});await cache.clear();await pending;
  assert.equal(records.size,0);assert.equal(await cache.read(),null);
});
test('a read already in progress cannot return plaintext after lock',async()=>{
  const {cache,store}=fixture();await cache.unlock(account,key);await cache.write({secret:'x'});
  const original=store.get; let release;
  store.get=async id=>{await new Promise(resolve=>release=resolve);return original(id);};
  const reading=cache.read();cache.lock();release();assert.equal(await reading,null);
});
test('cache size limit fails without replacing existing history',async()=>{
  const {cache}=fixture();await cache.unlock(account,key);await cache.write({secret:'kept'});
  await assert.rejects(cache.write({large:'x'.repeat(13*1024*1024)}));assert.deepEqual(await cache.read(),{secret:'kept'});
});

test('a blocked browser database times out instead of hanging sign-in and can retry',async()=>{
  const requests=[];
  const store=browserHistoryStore({open:()=>{const request={};requests.push(request);return request;}},10);
  await assert.rejects(store.get(account),/timed out/);
  let closed=false;
  requests[0].result={close:()=>closed=true};requests[0].onsuccess();assert.equal(closed,true);
  await assert.rejects(store.get(account),/timed out/);assert.equal(requests.length,2);
});
test('quota failure retains previous ciphertext and the next save can recover',async()=>{
  const {cache,store}=fixture();await cache.unlock(account,key);await cache.write({secret:'kept'});
  const put=store.put;store.put=async()=>{throw new DOMException('Full','QuotaExceededError');};
  await assert.rejects(cache.write({secret:'new'}),{name:'QuotaExceededError'});
  assert.deepEqual(await cache.read(),{secret:'kept'});
  store.put=put;await cache.write({secret:'recovered'});assert.deepEqual(await cache.read(),{secret:'recovered'});
});
