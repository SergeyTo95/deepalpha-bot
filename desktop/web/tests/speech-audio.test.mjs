import test from 'node:test';
import assert from 'node:assert/strict';
import {canStreamAudio,attachAudioStream,trackPlaybackStatus} from '../speech-audio.mjs';

test('playing without media-clock progress stays in preparation, even after repeated events',()=>{
 const audio={currentTime:0,paused:false},states=[];
 trackPlaybackStatus(audio,{valid:()=>true,speaking:()=>states.push('speaking'),buffering:()=>states.push('preparing')});
 audio.onplaying();for(let i=0;i<30;i++)audio.ontimeupdate();assert.deepEqual(states,[]);
 audio.currentTime=.02;audio.ontimeupdate();assert.deepEqual(states,[]);
 audio.currentTime=.1;audio.ontimeupdate();assert.deepEqual(states,['speaking']);
});
test('after buffering or pause, a player must advance again before showing speaking',()=>{
 const audio={currentTime:1,paused:false},states=[];
 const dispose=trackPlaybackStatus(audio,{valid:()=>true,speaking:()=>states.push('speaking'),buffering:()=>states.push('preparing')});
 audio.onplaying();assert.deepEqual(states,[]);audio.currentTime=1.1;audio.ontimeupdate();
 audio.onwaiting();audio.onplaying();assert.deepEqual(states,['speaking','preparing']);
 audio.currentTime=1.2;audio.ontimeupdate();assert.equal(states.at(-1),'speaking');
 audio.paused=true;audio.onpause();audio.currentTime=1.3;audio.ontimeupdate();assert.equal(states.at(-1),'preparing');dispose();
});
test('callbacks from a previous player or a stopped call cannot change the status',()=>{
 const audio={currentTime:0,paused:false},states=[];let live=true;
 const dispose=trackPlaybackStatus(audio,{valid:()=>live,speaking:()=>states.push('speaking'),buffering:()=>states.push('preparing')});
 const playing=audio.onplaying,progress=audio.ontimeupdate,waiting=audio.onwaiting;
 live=false;audio.currentTime=1;playing();progress();waiting();assert.deepEqual(states,[]);
 live=true;dispose();playing();progress();waiting();assert.deepEqual(states,[]);
 assert.equal(audio.onplaying,null);assert.equal(audio.ontimeupdate,null);
});

function setup(signal) {
 const sources=[],errors=[];
 class Buffer extends EventTarget {
  constructor(){super();this.parts=[];this.updating=false;}
  appendBuffer(value){assert.equal(this.updating,false);this.updating=true;this.parts.push(value);queueMicrotask(()=>{this.updating=false;this.dispatchEvent(new Event('updateend'));});}
  abort(){this.updating=false;}
 }
 class Source extends EventTarget {
  constructor(){super();sources.push(this);}
  addSourceBuffer(type){assert.equal(type,'audio/mpeg');return this.buffer=new Buffer();}
  endOfStream(){this.ended=true;}
 }
 let controller,cancelled=0;
 const body=new ReadableStream({start(c){controller=c;},cancel(){cancelled++;}});
 const url=URL.createObjectURL;URL.createObjectURL=()=> 'blob:fixture';
 let player;try{player=attachAudioStream({body},{signal,Source,onFailure:()=>errors.push('failed')});}finally{URL.createObjectURL=url;}
 return {player,source:sources[0],controller,body,errors,get cancelled(){return cancelled;}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('MP3 streaming is feature detected without breaking other browsers',()=>{
 assert.equal(canStreamAudio(null),false);assert.equal(canStreamAudio({isTypeSupported:()=>false}),false);
 assert.equal(canStreamAudio({isTypeSupported:type=>type==='audio/mpeg'}),true);
 assert.equal(canStreamAudio({isTypeSupported:()=>{throw Error();}}),false);
});
test('first audio bytes append before provider finishes; MP3 chunks append serially',async()=>{
 const s=setup();s.source.dispatchEvent(new Event('sourceopen'));
 s.controller.enqueue(new Uint8Array([1,2,3]));await tick();
 assert.deepEqual([...s.source.buffer.parts[0]],[1,2,3]);assert.equal(s.source.ended,undefined);
 s.controller.enqueue(new Uint8Array([4,5]));s.controller.close();await s.player.completion;
 assert.equal(s.source.buffer.parts.length,2);assert.equal(s.source.ended,true);assert.deepEqual(s.errors,[]);
 assert.equal(s.body.locked,false);
});
test('closing before MediaSource opens cancels audio without late failures',async()=>{
 const s=setup();s.player.cancel();s.source.dispatchEvent(new Event('sourceopen'));await s.player.completion;
 assert.equal(s.cancelled,1);assert.equal(s.source.buffer,undefined);assert.deepEqual(s.errors,[]);
});
test('abort while waiting for provider bytes releases the reader',async()=>{
 const c=new AbortController(),s=setup(c.signal);s.source.dispatchEvent(new Event('sourceopen'));await tick();
 c.abort();await s.player.completion;assert.equal(s.cancelled,1);assert.equal(s.body.locked,false);assert.deepEqual(s.errors,[]);
});
test('an already-aborted request cannot start playback',async()=>{
 const c=new AbortController();c.abort();const s=setup(c.signal);await s.player.completion;
 assert.equal(s.cancelled,1);assert.deepEqual(s.errors,[]);
});
test('a truncated provider stream fails without a successful end marker',async()=>{
 const s=setup();s.source.dispatchEvent(new Event('sourceopen'));s.controller.enqueue(new Uint8Array([1]));await tick();
 s.controller.error(Error('private upstream details'));await s.player.completion;
 assert.deepEqual(s.errors,['failed']);assert.equal(s.source.ended,undefined);
});
test('empty or oversized audio does not complete successfully',async()=>{
 for(const size of [0,2*1024*1024+1]){
  const s=setup();s.source.dispatchEvent(new Event('sourceopen'));
  if(size)s.controller.enqueue(new Uint8Array(size));s.controller.close();await s.player.completion;
  assert.deepEqual(s.errors,['failed']);assert.equal(s.source.ended,undefined);
 }
});
