import test from 'node:test';
import assert from 'node:assert/strict';
import {recordingType,createVoiceCapture} from '../voice-capture.mjs';
function fixture(t,{type='audio/mp4',pendingPermission=false,pendingUpload=false}={}) {
 t.mock.timers.enable({apis:['setTimeout']});
 const ready=[],errors=[],requests=[],recorders=[],tracks=[],constraints=[];let grant,reply;
 const stream=()=>{const track={stopped:false,stop(){this.stopped=true;}};tracks.push(track);return {getTracks:()=>[track]};};
 class Recorder {
  static isTypeSupported(candidate){return candidate===type;}
  constructor(source,options){this.mimeType=options.mimeType;recorders.push(this);}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['audio'],{type:this.mimeType})});this.onstop?.();}
 }
 const capture=createVoiceCapture({ready:text=>ready.push(text),error:code=>errors.push(code),Recorder,
  devices:{getUserMedia:async options=>{constraints.push(options);return pendingPermission?new Promise(resolve=>{grant=()=>resolve(stream());}):stream();}},
  send:async(url,options)=>{requests.push({url,...options});return pendingUpload?new Promise(resolve=>{reply=()=>resolve({ok:true,json:async()=>({text:'поздний ответ'})});}):{ok:true,json:async()=>({text:'Нет, 125,5 рублей'})};}});
 t.after(()=>capture.cancel());
 return {capture,ready,errors,requests,recorders,tracks,constraints,grant:()=>grant(),reply:()=>reply(),tick:ms=>t.mock.timers.tick(ms)};
}
test('Safari MP4 and Chromium/Firefox Opus formats are negotiated without user agent checks',()=>{
 for(const type of ['audio/mp4','audio/webm;codecs=opus','audio/ogg;codecs=opus'])assert.equal(recordingType({isTypeSupported:value=>value===type}),type);
 assert.equal(recordingType(class {}),'');
});
for(const type of ['audio/mp4','audio/webm;codecs=opus','audio/ogg;codecs=opus'])test(type+' sends a single complete recording and immediately releases the microphone',async t=>{
 const s=fixture(t,{type});await s.capture.start('ru-RU');
 assert.equal(s.requests.length,0);assert.equal(s.capture.recording,true);
 assert.deepEqual(s.constraints[0],{audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
 s.capture.finish();await new Promise(resolve=>queueMicrotask(resolve));await new Promise(resolve=>queueMicrotask(resolve));
 assert.equal(s.tracks[0].stopped,true);assert.equal(s.requests.length,1);assert.equal(s.requests[0].body.type,type);
 assert.match(s.requests[0].url,/language=ru$/);assert.deepEqual(s.ready,['Нет, 125,5 рублей']);assert.deepEqual(s.errors,[]);
});
test('closing while permission is pending stops a late stream without recording or uploading',async t=>{
 const s=fixture(t,{pendingPermission:true});const starting=s.capture.start('en-US');s.capture.cancel();s.grant();await starting;
 assert.ok(s.tracks.every(track=>track.stopped));assert.deepEqual(s.recorders,[]);assert.deepEqual(s.requests,[]);
});
test('mute/close cancels upload and fences a late transcript',async t=>{
 const s=fixture(t,{pendingUpload:true});await s.capture.start('tr-TR');s.capture.finish();s.capture.cancel();s.reply();
 await new Promise(resolve=>queueMicrotask(resolve));await new Promise(resolve=>queueMicrotask(resolve));
 assert.equal(s.requests[0].signal.aborted,true);assert.deepEqual(s.ready,[]);assert.deepEqual(s.errors,[]);
});
test('recording limit rejects the entire utterance without sending a truncated phrase',async t=>{
 const s=fixture(t);await s.capture.start('ru');s.tick(45000);
 assert.deepEqual(s.errors,['too_long']);assert.deepEqual(s.requests,[]);assert.ok(s.tracks.every(track=>track.stopped));
});
test('oversized audio is discarded before upload',async t=>{
 const s=fixture(t);await s.capture.start('ru');s.recorders[0].ondataavailable({data:new Blob([new Uint8Array(1024*1024+1)])});
 assert.deepEqual(s.errors,['too_large']);assert.deepEqual(s.requests,[]);
});
test('permission denial reports a useful error without falling into an automatic retry loop',async()=>{
 const errors=[];const capture=createVoiceCapture({ready(){},error:code=>errors.push(code),devices:{getUserMedia:async()=>{throw Object.assign(Error(),{name:'NotAllowedError'});}}});
 await capture.start('ru');assert.deepEqual(errors,['permission']);assert.equal(capture.recording,false);
});
