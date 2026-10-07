import test from 'node:test';
import assert from 'node:assert/strict';
import {speechChunks,availableVoices,voiceChoice,speechReady} from '../voice.mjs';
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

test('voice inventory is language scoped, deduplicated and represents real voices',()=>{
 const voices=[{name:'English',lang:'en-US',voiceURI:'en'}, {name:'Russian',lang:'ru-RU',voiceURI:'ru',localService:true}, {name:'Same',lang:'ru-RU',voiceURI:'ru'}];
 assert.equal(availableVoices(voices,'ru-RU').length,1);
 assert.equal(voiceChoice(voices,'ru-RU','').voiceURI,'ru');
 assert.equal(voiceChoice(voices,'ru-RU','en').voiceURI,'en');
 assert.equal(voiceChoice(voices,'tr-TR',''),null);
 voices.push({name:'Second Russian',lang:'ru-RU',voiceURI:'ru-second'});
 assert.equal(availableVoices(voices,'ru-RU').length,2);
 assert.notEqual(voiceChoice(voices,'ru-RU','ru').voiceURI,voiceChoice(voices,'ru-RU','ru-second').voiceURI);
});
test('emoji sequences are silent while punctuation and numbers remain',()=>{
 const text='Привет! 😊 ❤️ 👩🏽‍💻 🇹🇷 1️⃣ Цена 125 рублей.';
 assert.equal(speechChunks(text).join(' '),'Привет! Цена 125 рублей.');
 assert.deepEqual(speechChunks('😊'),[]);
});
test('streaming releases complete phrases and holds code and unfinished words',()=>{
 assert.equal(speechReady('Привет! Ещё'),7);
 assert.equal(speechReady('Думаю над ответом'),0);
 assert.equal(speechReady('```js\ncode();'),0);
 assert.equal(speechReady('Хвост',true),5);
});
