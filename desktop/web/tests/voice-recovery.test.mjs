import test from 'node:test';
import assert from 'node:assert/strict';
import {setupVoice} from '../voice.mjs';
import {readCompletion} from '../core.mjs';
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function fixture(t,{responder,speech=false,remote=false,recognition='standard',recording=false}={}) {
 t.mock.timers.enable({apis:['setTimeout']});
 const recognizers=[],sent=[],events=[],spoken=[],preparations=[],media=[],sockets=[],httpSpeech=[],recorders=[],tracks=[],transcriptions=[];let completion;
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
 class Recorder {
  static isTypeSupported(type){return type==='audio/mp4';}
  constructor(stream,options){this.mimeType=options.mimeType;recorders.push(this);}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['fixture'],{type:this.mimeType})});this.onstop?.();}
 }
 const body=new Element('body'), doc={body,hidden:false,documentElement:{lang:'ru'},
  createElement:tag=>new Element(tag),addEventListener(){}};
 const synth={getVoices:()=>[{voiceURI:'local-ru',name:'Local',lang:'ru-RU',localService:true}],
  addEventListener(){},cancel(){},resume(){},speak(u){if(u.volume===0)return;spoken.push(u);u.onstart?.();}};
 class Audio {
  constructor(){this.currentTime=0;this.paused=true;}
  pause(){this.paused=true;this.onpause?.();}
  removeAttribute(){this.src='';}
  load(){this.currentTime=0;}
  play(){
   if(this.src.startsWith('data:'))return Promise.resolve();
   this.paused=false;media.push({player:this,ended:this.onended});this.onplaying?.();
   this.currentTime=.1;this.ontimeupdate?.();return Promise.resolve();
  }
 }
 class Socket {
  constructor(){this.readyState=0;this.sent=[];sockets.push(this);queueMicrotask(()=>{if(this.readyState===3)return;this.readyState=1;this.onopen();});}
  send(value){
   const data=JSON.parse(value);this.sent.push(data);if(data.type!=='speak')return;
   queueMicrotask(()=>{
    if(this.readyState!==1)return;
    this.onmessage({data:JSON.stringify({type:'start',id:data.id})});
    const audio=new Uint8Array(7);new DataView(audio.buffer).setUint32(0,data.id);audio.set([1,2,3],4);
    this.onmessage({data:audio.buffer});this.onmessage({data:JSON.stringify({type:'end',id:data.id})});
   });
  }
  close(){this.readyState=3;this.onclose?.();}
 }
 const globals={window:{...(recognition==='webkit'?{webkitSpeechRecognition:Recognition}:recognition==='standard'?{SpeechRecognition:Recognition}:{}),...(speech?{speechSynthesis:synth}:{})},document:doc,
  ...(recording?{MediaRecorder:Recorder}:{}),
  ...(remote?{Audio,WebSocket:Socket,location:{href:'https://velia.example/'}}:{}),
  SpeechSynthesisUtterance:class {constructor(text){this.text=text;}},
  navigator:{language:'ru-RU',...(recording?{mediaDevices:{getUserMedia:async()=>{const track={stopped:false,stop(){this.stopped=true;}};tracks.push(track);return {getTracks:()=>[track]};}}}:{})},localStorage:{getItem:()=>null},
  fetch:async(url,options)=>{if(url.includes('/transcribe?')){transcriptions.push(options);return {ok:true,json:async()=>({text:'Не отправляй 125 рублей'})};}if(url.endsWith('/events'))events.push(JSON.parse(options.body));if(url.endsWith('/prepare'))preparations.push(JSON.parse(options.body));if(url.includes('/speech')){httpSpeech.push(url);throw Error('unexpected HTTP speech');}return {ok:true,json:async()=>remote?{voices:[{voiceURI:'edge:ru-RU-SvetlanaNeural',lang:'ru-RU',name:'Светлана'}],channel:true}:{voices:[],transcription:recording}};}};
 const previous=new Map();
 for(const [key,value] of Object.entries(globals)){previous.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});}
 const voice=setupVoice({send:text=>{sent.push(text);return completion=responder?responder(voice):Promise.resolve(voice.complete(''));},busy:()=>false,toast(){},accountLanguage:()=> 'ru'});
 t.after(()=>{voice.stop();for(const [key,descriptor] of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}});
 voice.open();
 return {voice,recognizers,sent,events,spoken,preparations,media,sockets,httpSpeech,recorders,tracks,transcriptions,
  transcript:()=>body.children[0].children.find(n=>n.className==='voice-transcript'),
  record:()=>body.children[0].children.find(n=>n.className==='voice-record'),
  native:()=>body.children[0].children.find(n=>n.className==='voice-native'),
  mute:()=>body.children[0].children.find(n=>n.className==='voice-mute'),get completion(){return completion;},tick:ms=>t.mock.timers.tick(ms),status:()=>body.children[0].children.find(n=>n.tag==='p'&&n.className==='voice-status')?.textContent};
}

test('Safari prefixed recognition displays revisions but sends only the final complete phrase once',t=>{
 const s=fixture(t,{recognition:'webkit'}),r=s.recognizers[0];
 r.onresult({results:[Object.assign([{transcript:'Не от',confidence:0}],{isFinal:false})]});
 assert.equal(s.transcript().textContent,'Не от');assert.deepEqual(s.sent,[]);
 r.onresult({results:[Object.assign([{transcript:'Не отправляй 125 рублей'}],{isFinal:true})]});r.onend();r.onend();
 assert.deepEqual(s.sent,['Не отправляй 125 рублей']);
});

test('without native recognition the Safari recording fallback waits for a gesture and returns to manual listening after an answer',async t=>{
 const s=fixture(t,{recognition:'none',recording:true,speech:true,responder:async voice=>{voice.update('Хорошо, не отправляю.');voice.complete('Хорошо, не отправляю.');}});
 await flush();assert.equal(s.record().hidden,false);assert.equal(s.recorders.length,0);
 s.record().onclick();await flush();assert.equal(s.recorders.length,1);assert.equal(s.recorders[0].mimeType,'audio/mp4');
 s.record().onclick();await flush();await s.completion;
 assert.deepEqual(s.sent,['Не отправляй 125 рублей']);assert.equal(s.transcriptions.length,1);assert.equal(s.tracks[0].stopped,true);
 assert.equal(s.transcript().textContent,'Не отправляй 125 рублей');assert.deepEqual(s.spoken.map(u=>u.text),['Хорошо, не отправляю.']);
 s.spoken[0].onend();s.tick(400);assert.equal(s.recorders.length,1);assert.match(s.status(),/Записать реплику/);
});

test('muting during a fallback recording discards captured audio and late recorder events',async t=>{
 const s=fixture(t,{recognition:'none',recording:true});await flush();s.record().onclick();await flush();s.mute().onclick();await flush();
 assert.deepEqual(s.sent,[]);assert.deepEqual(s.transcriptions,[]);assert.ok(s.tracks.every(track=>track.stopped));
 assert.equal(s.transcript().hidden,true);
});

test('switching from manual recording back to native recognition cancels captured audio',async t=>{
 const s=fixture(t,{recognition:'webkit',recording:true});await flush();
 const old=s.recognizers[0];s.record().onclick();await flush();assert.equal(s.native().hidden,false);
 s.native().onclick();await flush();assert.equal(s.recognizers.length,2);assert.equal(s.native().hidden,true);
 old.onresult({results:[Object.assign([{transcript:'Старая реплика'}],{isFinal:true})]});old.onend();
 assert.deepEqual(s.sent,[]);assert.deepEqual(s.transcriptions,[]);assert.ok(s.tracks.every(track=>track.stopped));
});

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

test('muting invalidates captured speech and late recognition callbacks until explicitly resumed',t=>{
 const s=fixture(t),old=s.recognizers[0];
 old.onresult({results:[Object.assign([{transcript:'Фоновая реплика'}],{isFinal:true})]});
 s.mute().onclick();old.onend();old.onerror({error:'network'});s.tick(10000);
 assert.deepEqual(s.sent,[]);assert.deepEqual(s.events,[]);assert.equal(s.recognizers.length,1);
 assert.equal(s.mute().textContent,'Включить микрофон');assert.match(s.status(),/Микрофон выключен/);
 s.mute().onclick();assert.equal(s.recognizers.length,2);assert.equal(s.recognizers[1].started,true);
 old.onresult({results:[Object.assign([{transcript:'Поздняя реплика'}],{isFinal:true})]});old.onend();
 assert.deepEqual(s.sent,[]);
});

test('muting during an answer preserves playback and does not automatically reopen the microphone',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{voice.update('Продолжаю ответ.');voice.complete('Продолжаю ответ.');}});
 submit(s);await s.completion;s.mute().onclick();
 assert.deepEqual(s.spoken.map(u=>u.text),['Продолжаю ответ.']);
 s.spoken[0].onend();s.tick(400);assert.equal(s.recognizers.length,1);assert.match(s.status(),/Микрофон выключен/);
 s.mute().onclick();assert.equal(s.recognizers.length,2);assert.equal(s.status(),'Слушаю тебя…');
});

test('muting cancels pending network recovery and a new conversation starts with microphone enabled',t=>{
 const s=fixture(t);s.recognizers[0].onerror({error:'network'});s.mute().onclick();s.tick(10000);
 assert.equal(s.recognizers.length,1);s.voice.stop();s.voice.open();
 assert.equal(s.recognizers.length,2);assert.equal(s.mute().textContent,'Выключить микрофон');
});

test('non-speech result markers never send a model request or end the conversation',t=>{
 const s=fixture(t);
 for(const text of ['…','[noise]','[музыка]','[silence] [noise]']){
  const r=s.recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:text}],{isFinal:true})]});r.onend();s.tick(800);
  assert.deepEqual(s.sent,[]);assert.equal(s.recognizers.at(-1).started,true);
 }
 const r=s.recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Нет',confidence:0}],{isFinal:true})]});r.onend();
 assert.deepEqual(s.sent,['Нет']);
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

test('canonical tail correction keeps an unchanged greeting in flight instead of greeting twice',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{
  voice.update('Привет! Черновое продолжение.');
  voice.complete('Привет! Система работает нормально.');
 }});
 submit(s);await s.completion;
 assert.deepEqual(s.spoken.map(u=>u.text),['Привет!']);
 s.spoken[0].onend();s.spoken[0].onend();assert.deepEqual(s.spoken.map(u=>u.text),['Привет!','Система работает нормально.']);
 s.spoken[1].onend();s.tick(400);assert.equal(s.recognizers.length,2);
});

test('a split canonical SSE replacement preserves the shared greeting through update and completion',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{
  const frames=[{choices:[{delta:{content:'Привет! Черновой факт.'}}]}, {reset:true},
   {choices:[{delta:{content:'Привет! Окончательный факт.'},finish_reason:'stop'}]}];
  const stream=new ReadableStream({start(c){for(const event of frames)c.enqueue(new TextEncoder().encode('data: '+JSON.stringify(event)+'\n\n'));c.enqueue(new TextEncoder().encode('data: [DONE]\n\n'));c.close();}});
  const result=await readCompletion(new Response(stream,{headers:{'content-type':'text/event-stream'}}),text=>voice.update(text));
  voice.complete(result.text);
 }});
 submit(s);await s.completion;assert.deepEqual(s.spoken.map(u=>u.text),['Привет!']);
 s.spoken[0].onend();assert.deepEqual(s.spoken.map(u=>u.text),['Привет!','Окончательный факт.']);
 s.spoken[1].onend();s.tick(400);assert.equal(s.recognizers.length,2);
});

test('an incomplete replacement prefix does not interrupt an unchanged current phrase',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{
  voice.update('Привет! Старый факт.');voice.update('При');
  voice.update('Привет! Новый факт.');voice.complete('Привет! Новый факт.');
 }});
 submit(s);await s.completion;assert.deepEqual(s.spoken.map(u=>u.text),['Привет!']);
 s.spoken[0].onend();assert.deepEqual(s.spoken.map(u=>u.text),['Привет!','Новый факт.']);
 s.spoken[1].onend();s.tick(400);assert.equal(s.recognizers.length,2);
});

test('completed common phrases stay spoken once when the canonical tail changes later',async t=>{
 let release;const waiting=new Promise(resolve=>{release=resolve;});
 const s=fixture(t,{speech:true,responder:async voice=>{
  voice.update('Привет! Продолжаю. Старый факт.');await waiting;
  voice.complete('Привет! Продолжаю. Новый факт.');
 }});
 submit(s);s.spoken[0].onend();s.spoken[1].onend();release();await s.completion;
 assert.deepEqual(s.spoken.map(u=>u.text),['Привет!','Продолжаю.','Старый факт.','Новый факт.']);
 const stale=s.spoken[2].onend;stale();s.tick(400);assert.equal(s.recognizers.length,1);
 s.spoken[3].onend();s.tick(400);assert.equal(s.recognizers.length,2);
});

test('a remote canonical correction keeps the current player and socket while replacing prefetched tail',async t=>{
 let release;const waiting=new Promise(resolve=>{release=resolve;});
 const s=fixture(t,{remote:true,responder:async voice=>{
  voice.update('Привет! Черновой факт.');await waiting;
  voice.complete('Привет! Окончательный факт.');
 }});
 const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();submit(s);await flush();
 assert.equal(s.media.length,1);const player=s.media[0].player;
 release();await s.completion;await flush();
 assert.equal(s.media.length,1);assert.equal(s.sockets.length,1);assert.equal(s.sockets[0].readyState,1);
 s.media[0].ended();await flush();assert.equal(s.media.length,2);assert.equal(s.media[1].player,player);
 s.media[1].ended();s.tick(400);assert.equal(s.recognizers.length,2);
 assert.equal(s.sockets[0].sent.filter(x=>x.type==='speak'&&x.text==='Привет!').length,1);
 assert.deepEqual(s.httpSpeech,[]);
});

test('identical greetings in separate voice turns are both spoken and are never globally deduplicated',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{voice.update('Привет!');voice.complete('Привет!');}});
 for(let turn=0;turn<2;turn++){
  submit(s);await s.completion;s.spoken[turn].onend();s.tick(400);
 }
 assert.deepEqual(s.spoken.map(u=>u.text),['Привет!','Привет!']);assert.equal(s.recognizers.length,3);
});

test('opening voice prepares transport without speech text or blocking the microphone',t=>{
 const s=fixture(t);assert.deepEqual(s.preparations,[{}]);
 assert.equal(s.recognizers.length,1);assert.equal(s.recognizers[0].started,true);assert.deepEqual(s.sent,[]);
 s.voice.open();assert.equal(s.recognizers.length,1);assert.equal(s.preparations.length,1);
});

test('one playback timing per turn contains only model, clock and relative durations',async t=>{
 const s=fixture(t,{speech:true,responder:async voice=>{voice.update('Привет!');voice.complete('Привет!');}});
 submit(s);await s.completion;
 const events=s.events.filter(event=>event.event==='voice_latency');assert.equal(events.length,1);
 assert.deepEqual(Object.keys(events[0]).sort(),['event','model','clock','recognition_ms','first_text_ms','first_audio_ms'].sort());
 assert.equal(events[0].model,'flash');assert.equal(events[0].clock,'device');
 assert.ok(events[0].recognition_ms<=events[0].first_text_ms&&events[0].first_text_ms<=events[0].first_audio_ms);
 s.spoken[0].onstart();assert.equal(s.events.filter(event=>event.event==='voice_latency').length,1);
});

test('three remote phrases use one answer channel, preserve order and resume the microphone once',async t=>{
 const text='Первая фраза. Вторая фраза. Третья фраза.';
 const s=fixture(t,{remote:true,responder:async voice=>{voice.update(text);voice.complete(text);}});
 const flush=()=>new Promise(resolve=>setImmediate(resolve));await flush();
 submit(s);await s.completion;await flush();
 assert.equal(s.media.length,1);assert.equal(s.sockets.length,1);
 s.media[0].ended();await flush();assert.equal(s.media.length,2);
 s.media[0].ended();await flush();assert.equal(s.media.length,2);
 s.media[1].ended();await flush();assert.equal(s.media.length,3);
 s.media[2].ended();s.tick(400);
 assert.deepEqual(s.sockets[0].sent.filter(x=>x.type==='speak').map(x=>x.text),['Первая фраза.','Вторая фраза.','Третья фраза.']);
 assert.equal(s.sockets[0].readyState,3);assert.deepEqual(s.httpSpeech,[]);
 assert.equal(s.recognizers.length,2);assert.equal(s.status(),'Слушаю тебя…');
 assert.equal(s.events.filter(x=>x.event==='voice_latency'&&x.clock==='media').length,1);
});
