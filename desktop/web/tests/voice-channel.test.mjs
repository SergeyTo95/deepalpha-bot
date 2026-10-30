import test from 'node:test';
import assert from 'node:assert/strict';
import {createSpeechChannel,prefetchDepth} from '../voice-channel.mjs';

const fields={text:'Полная фраза без изменений.',voice:'edge:ru-RU-SvetlanaNeural',rate:1};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(fallback=async()=>{throw Error('unexpected fallback');}) {
 const sockets=[];
 class Socket {
  constructor(url,protocol){this.url=url;this.protocol=protocol;this.readyState=0;this.sent=[];sockets.push(this);}
  send(value){this.sent.push(JSON.parse(value));}
  open(){this.readyState=1;this.onopen();}
  close(){this.readyState=3;this.onclose?.();}
  message(value){this.onmessage({data:typeof value==='object'?JSON.stringify(value):value});}
  audio(id,bytes){const value=new Uint8Array(4+bytes.length);new DataView(value.buffer).setUint32(0,id);value.set(bytes,4);this.onmessage({data:value.buffer});}
 }
 const channel=createSpeechChannel({Socket,fallback,location:{href:'https://velia.example/web/'}});
 return {channel,sockets};
}

test('two exact speech jobs share one same-origin channel and retain separate progressive audio',async()=>{
 const s=fixture();
 try{
  const first=s.channel.request(fields),second=s.channel.request({...fields,text:'Следующая фраза.'});
  assert.equal(s.sockets.length,1);const ws=s.sockets[0];
  assert.equal(ws.url,'wss://velia.example/web-api/v1/voice/stream');assert.equal(ws.protocol,'velia-voice-v1');
  ws.open();await tick();assert.deepEqual(ws.sent.map(({type,id,...data})=>data),[fields,{...fields,text:'Следующая фраза.'}]);
  ws.message({type:'start',id:1});ws.message({type:'start',id:2});
  const a=await first,b=await second;const ar=a.response.arrayBuffer(),br=b.response.arrayBuffer();
  ws.audio(2,[3,4]);ws.audio(1,[1,2]);ws.message({type:'end',id:1});ws.message({type:'end',id:2});
  assert.deepEqual([...new Uint8Array(await ar)],[1,2]);assert.deepEqual([...new Uint8Array(await br)],[3,4]);
 }finally{s.channel.close();}
});

test('answer preparation performs the handshake without sending any speech',async()=>{
 const s=fixture();
 try{
  s.channel.prepare();s.channel.prepare();assert.equal(s.sockets.length,1);
  s.sockets[0].open();await tick();assert.deepEqual(s.sockets[0].sent,[]);
  const pending=s.channel.request(fields);await tick();assert.equal(s.sockets[0].sent.length,1);
  s.channel.close();assert.equal(await pending,null);
 }finally{s.channel.close();}
});

test('an unavailable channel falls back once before audio with the same fields and cancellation signal',async()=>{
 const calls=[],s=fixture(async(data,signal)=>{calls.push([data,signal]);return {blob:new Blob(['legacy'])};});
 const controller=new AbortController();
 try{
  const pending=s.channel.request(fields,{signal:controller.signal});s.sockets[0].close();
  assert.equal(await (await pending).blob.text(),'legacy');assert.deepEqual(calls,[[fields,controller.signal]]);
 }finally{s.channel.close();}
});

test('partial audio failure is reported without HTTP replay',async()=>{
 const calls=[],s=fixture(async()=>{calls.push(true);return null;});
 try{
  const pending=s.channel.request(fields);s.sockets[0].open();await tick();
  s.sockets[0].message({type:'start',id:1});const audio=await pending;
  const body=audio.response.arrayBuffer();const failure=assert.rejects(body,/tts_unavailable/);
  s.sockets[0].audio(1,[1,2]);s.sockets[0].close();await failure;assert.deepEqual(calls,[]);
 }finally{s.channel.close();}
});

test('server rate limits and user cancellation never trigger another synthesis request',async()=>{
 const s=fixture();
 try{
  const controller=new AbortController(),cancelled=s.channel.request(fields,{signal:controller.signal});
  s.sockets[0].open();await tick();controller.abort();assert.equal(await cancelled,null);
  assert.deepEqual(s.sockets[0].sent.at(-1),{type:'cancel',id:1});
  const limited=s.channel.request(fields);await tick();
  s.sockets[0].message({type:'error',id:2,code:'tts_rate_limit',partial:false});
  assert.equal(await limited,null);
 }finally{s.channel.close();}
});

test('buffered playback waits for a complete segment and queue depth remains bounded',async()=>{
 const s=fixture();
 try{
  let ready=false;const first=s.channel.request(fields,{streaming:false}).then(value=>{ready=true;return value;});
  const second=s.channel.request(fields),third=s.channel.request(fields);
  assert.equal(await s.channel.request(fields),null);
  s.sockets[0].open();await tick();s.sockets[0].message({type:'start',id:1});s.sockets[0].audio(1,[1,2,3]);
  await tick();assert.equal(ready,false);s.sockets[0].message({type:'end',id:1});
  assert.deepEqual([...new Uint8Array(await (await first).blob.arrayBuffer())],[1,2,3]);
  s.channel.close();assert.equal(await second,null);assert.equal(await third,null);
 }finally{s.channel.close();}
});

test('adaptive prefetch prepares two phrases during long playback and one near a boundary',()=>{
 assert.equal(prefetchDepth(10,1000),2);assert.equal(prefetchDepth(.5,1000),1);
 assert.equal(prefetchDepth(4,3000),1);assert.equal(prefetchDepth(4,300),2);
 assert.equal(prefetchDepth(NaN,300),1);assert.equal(prefetchDepth(Infinity,300),1);
});
