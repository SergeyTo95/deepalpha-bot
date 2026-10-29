import test from 'node:test';
import assert from 'node:assert/strict';
import {bindRecognitionTurn} from '../voice.mjs';

function session() {
 let now=0,next=0,live=true,stops=0,aborts=0,misses=0;
 const timers=new Map(),finished=[];
 const r={stop(){stops++;},abort(){aborts++;r.onend?.();}};
 const cancel=bindRecognitionTurn(r,{valid:()=>live,finish:text=>finished.push(text),incomplete:()=>{misses++;},
  setTimer:(fn,ms)=>{const id=++next;timers.set(id,{fn,at:now+ms});return id;},clearTimer:id=>timers.delete(id)});
 function tick(ms){const end=now+ms;while(true){const job=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!job)break;now=job[1].at;timers.delete(job[0]);job[1].fn();}now=end;}
 const result=(...parts)=>r.onresult({results:parts.map(([transcript,isFinal=true])=>Object.assign([{transcript}],{isFinal}))});
 return {r,cancel,tick,result,finished,counts:()=>({stops,aborts,misses}),invalidate:()=>{live=false;}};
}
test('a conversational pause and resumed speech do not cut off the sentence',()=>{
 const s=session();s.result(['Я хочу']);s.tick(600);assert.equal(s.counts().stops,0);
 s.r.onspeechstart();s.tick(1500);assert.equal(s.counts().stops,0);
 s.result(['Я хочу'],['обсудить проект',false]);s.r.onspeechend();s.tick(1500);
 assert.equal(s.counts().stops,0);s.result(['Я хочу'],['обсудить проект']);s.tick(1000);
 assert.equal(s.counts().stops,1);assert.deepEqual(s.finished,[]);
 s.r.onend();assert.deepEqual(s.finished,['Я хочу обсудить проект']);
});
test('stop waits for the final captured words and end, then submits exactly once',()=>{
 const s=session();s.result(['Привет']);s.tick(1000);assert.deepEqual(s.finished,[]);
 s.result(['Привет'],['Велия',false]);s.result(['Привет'],['Велия']);s.r.onend();s.r.onend();s.tick(3000);
 assert.deepEqual(s.finished,['Привет Велия']);assert.deepEqual(s.counts(),{stops:1,aborts:0,misses:0});
});
test('an unfinished last segment rejects the whole turn instead of submitting a prefix',()=>{
 const s=session();s.result(['Отправь'],['документ',false]);s.r.onend();s.tick(5000);
 assert.deepEqual(s.finished,[]);assert.equal(s.counts().misses,1);
});
test('a missing browser end event does not silently submit potentially truncated text',()=>{
 const s=session();s.result(['Привет']);s.tick(3000);s.r.onend();s.result(['Поздние слова']);
 assert.deepEqual(s.finished,[]);assert.deepEqual(s.counts(),{stops:1,aborts:1,misses:1});
});
test('cancel and stale recognition events cannot submit or restart stop timers',()=>{
 for(const invalidate of [false,true]){
  const s=session();s.result(['Старый разговор']);if(invalidate)s.invalidate();else s.cancel();
  s.tick(5000);s.result(['Ещё слова']);s.r.onspeechend();s.r.onend();s.tick(5000);
  assert.deepEqual(s.finished,[]);assert.deepEqual(s.counts(),{stops:0,aborts:0,misses:0});
 }
});
test('synchronous browser end during stop does not leave a watchdog or duplicate submission',()=>{
 const s=session();s.r.stop=()=>s.r.onend();s.result(['Merhaba dünya']);s.tick(5000);
 assert.deepEqual(s.finished,['Merhaba dünya']);assert.equal(s.counts().misses,0);
});
test('updated interim hypotheses are replaced by the final multilingual result',()=>{
 const s=session();s.result(['السلام',false]);s.result(['السلام عليكم']);s.r.onend();
 assert.deepEqual(s.finished,['السلام عليكم']);
});
test('a browser that cannot stop enters recovery without submitting the partial turn',()=>{
 const s=session();s.r.stop=()=>{throw Error('engine stopped');};s.result(['Недописанная фраза']);s.tick(5000);
 assert.deepEqual(s.finished,[]);assert.equal(s.counts().misses,1);assert.equal(s.counts().aborts,1);
});
