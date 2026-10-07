import test from 'node:test';
import assert from 'node:assert/strict';
import {speechChunks} from '../voice.mjs';
test('long speech is bounded and preserves all plain words',()=>{
  const text=Array.from({length:200},(_,i)=>`слово${i}`).join(' ');
  const chunks=speechChunks(text);
  assert.ok(chunks.length>1);assert.ok(chunks.every(x=>x.length<=240));assert.equal(chunks.join(' '),text);
});
test('speech removes code and URLs while retaining link captions',()=>{
  const result=speechChunks('**Привет** [источник](https://example.com). ```js\nsecret();\n``` https://example.com').join(' ');
  assert.ok(result.includes('Привет источник'));assert.ok(result.includes('Код сохранён в диалоге.'));assert.ok(!result.includes('secret'));assert.ok(!result.includes('https://'));
});
test('empty reply and unclosed code are handled safely',()=>{
  assert.deepEqual(speechChunks('  '),[]);assert.deepEqual(speechChunks('```js\nprivate()'),['Код сохранён в диалоге.']);
  assert.ok(speechChunks('x'.repeat(900)).every(x=>x.length<=240));
});
