import test from 'node:test';
import assert from 'node:assert/strict';
import {setupVoice} from '../voice.mjs';
import {readCompletion} from '../core.mjs';

function fixture(t,{responder,speech=false}={}) {
 t.mock.timers.enable({apis:['setTimeout']});
 const recognizers=[],sent=[],events=[],spoken=[];let completion;
 class Element {
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.isConnected=true;}
  append(...nodes){this.children.push(...nodes);}
  setAttribute(){}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  showModal(){this.open=true;}
  close(){this.open=false;this.listeners.close?.();}
 }
 class Recognition {
  constructor(){recognizers.push(this);}
  start(){this.started=true;}
  abort(){this.onend?.();}
  stop(){this.onend?.();}
 }
 const body=new Element('body'), doc={body,hidden:false,documentElement:{lang:'ru'},
  createElement:tag=>new Element(tag),addEventListener(){}};
 const synth={getVoices:()=>[{voiceURI:'local-ru',name:'Local',lang:'ru-RU',localService:true}],
  addEventListener(){},cancel(){},resume(){},speak(u){if(u.volume===0)return;spoken.push(u);u.onstart?.();}};
 const globals={window:{SpeechRecognition:Recognition,...(speech?{speechSynthesis:synth}:{})},document:doc,
  SpeechSynthesisUtterance:class {constructor(text){this.text=text;}},
  navigator:{language:'ru-RU'},localStorage:{getItem:()=>null},
  fetch:async(url,options)=>{if(options?.body)events.push(JSON.parse(options.body));return {ok:true,json:async()=>({voices:[]})};}};
 const previous=new Map();
 for(const [key,value] of Object.entries(globals)){previous.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});}
 const voice=setupVoice({send:text=>{sent.push(text);return completion=responder?responder(voice):Promise.resolve(voice.complete(''));},busy:()=>false,toast(){},accountLanguage:()=> 'ru'});
 t.after(()=>{voice.stop();for(const [key,descriptor] of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
 voice.open();
 return {voice,recognizers,sent,events,spoken,get completion(){return completion;},tick:ms=>t.mock.timers.tick(ms),status:()=>body.children[0].children.find(n=>n.tag==='p'&&n.className==='voice-status')?.textContent};
}

test('temporary recognition failure reconnects without submitting an unfinished phrase',t=>{
 const s=fixture(t),old=s.recognizers[0];
 old.onresult({results:[Object.assign([{transcript:'Не отправляй'}],{isFinal:false})]});
 old.onerror({error:'network'});old.onend();
 assert.deepEqual(s.sent,[]);assert.equal(s.recognizers.length,1);
 s.tick(750);assert.equal(s.recognizers.length,2);assert.equal(s.recognizers[1].started,true);
 old.onresult({results:[Object.assign([{transcript:'старое сообщение'}],{isFinal:true})]});old.onend();
 assert.deepEqual(s.sent,[]);
 const current=s.recognizers[1];current.onresult({results:[Object.assign([{transcript:'Повторная фраза'}],{isFinal:true})]});current.onend();
 assert.deepEqual(s.sent,['Повторная фраза']);
 assert.deepEqual(s.events,[{event:'recognition_error',code:'network',attempt:1}]);
});

test('network recovery is bounded and never launches two microphones at once',t=>{
 const s=fixture(t);
 for(let attempt=1;attempt<=3;attempt++){
  s.recognizers.at(-1).onerror({error:'network'});s.tick(attempt*750);
  assert.equal(s.recognizers.length,Math.min(attempt+1,3));
 }
 s.tick(10000);assert.equal(s.recognizers.length,3);assert.deepEqual(s.sent,[]);
 assert.deepEqual(s.events.map(e=>e.attempt),[1,2,3]);
});

test('permission denial never retries and stopping cancels a scheduled recovery',t=>{
 const s=fixture(t);s.recognizers[0].onerror({error:'not-allowed'});s.tick(10000);
 assert.equal(s.recognizers.length,1);
 s.voice.open();assert.equal(s.recognizers.length,2);
 s.recognizers[1].onerror({error:'aborted'});s.voice.stop();s.tick(10000);
 assert.equal(s.recognizers.length,2);assert.deepEqual(s.sent,[]);
});

function submit(s){const r=s.recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Привет'}],{isFinal:true})]});r.onend();}

test('split canonical reset keeps speech running and returns to the microphone',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{
  const frames=[{choices:[{delta:{content:'Привет!'}}]}, {reset:true},
   {choices:[{delta:{content:'Привет!'},finish_reason:'stop'}]}];
  const body=new ReadableStream({start(c){for(const event of frames)c.enqueue(new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n'));c.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));c.close();}});
  const result=await readCompletion(new Response(body,{headers:{'content-type':'text/event-stream'}}),text=>voice.update(text));
  voice.complete(result.text);
 }});
 submit(s);await s.completion;
 assert.deepEqual(s.spoken.map(u=>u.text),['Привет!']);
 s.spoken[0].onend();s.tick(400);assert.equal(s.recognizers.length,2);
 assert.equal(s.status(),'Слушаю тебя…');
});

test('a genuinely corrected final answer is spoken once without ending the call',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{
  voice.update('Черновой ответ.');voice.update('Уточнение.');voice.complete('Уточнение.');
 }});
 submit(s);await s.completion;
 assert.deepEqual(s.spoken.map(u=>u.text),['Черновой ответ.','Уточнение.']);
 const stale=s.spoken[0].onend;stale();s.tick(400);assert.equal(s.recognizers.length,1);
 s.spoken[1].onend();s.tick(400);assert.equal(s.recognizers.length,2);
 assert.equal(s.status(),'Слушаю тебя…');
});

test('a correction arriving only at completion is delivered and does not leave the call stuck',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{voice.update('Первый ответ.');voice.complete('Итоговый ответ.');}});
 submit(s);await s.completion;
 assert.deepEqual(s.spoken.map(u=>u.text),['Первый ответ.','Итоговый ответ.']);
 s.spoken[1].onend();s.tick(400);assert.equal(s.status(),'Слушаю тебя…');
});
