/** Synthetic DOM/audio acceptance. Does not call or qualify SpeechKit. */
import assert from 'node:assert/strict';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM('<body><section id="settings"></section>',{url:'https://velia.example',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
const recognizers=[],audios=[],requests=[],spoken=[],released=[];
let delayed=false,resolveAudio;
window.SpeechRecognition=class{constructor(){recognizers.push(this);}start(){}stop(){this.onend?.();}abort(){this.onend?.();}};
globalThis.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
window.speechSynthesis={getVoices:()=>[{name:'Device',voiceURI:'device',lang:'ru-RU',localService:true}],addEventListener(){},speak:u=>spoken.push(u),cancel(){},resume(){}};
globalThis.Audio=class{constructor(url){this.url=url;audios.push(this);}async play(){this.played=true;}pause(){this.paused=true;}};
URL.createObjectURL=()=>`blob:${audios.length}`;URL.revokeObjectURL=url=>released.push(url);
const voices=['jane','omazh','marina'].map(id=>({voiceURI:'speechkit:'+id,name:id,lang:'ru-RU',localService:false}));
globalThis.fetch=async(url,options)=>{if(url.endsWith('/voices'))return {ok:true,json:async()=>({voices})};requests.push({data:JSON.parse(options.body),options});if(delayed)return new Promise(resolve=>{resolveAudio=()=>resolve({ok:true,blob:async()=>new Blob(['fixture'])});});return {ok:true,blob:async()=>new Blob(['fixture'])};};
localStorage.setItem('velia-voice-language','ru-RU');localStorage.setItem('velia-voice-uri','speechkit:omazh');
const {setupVoice}=await import('../web/voice.mjs');
let voice=setupVoice({send:async()=>{voice.update('Привет! 😊 ');voice.complete('Привет! 😊 Рада тебя слышать.');},busy:()=>false,toast:()=>{}});
voice.settings(document.querySelector('#settings'));voice.open();
const tick=()=>new Promise(resolve=>setTimeout(resolve,10));await tick();
assert.equal(document.querySelector('.voice-dialog select').value,'speechkit:omazh');
assert.equal(document.querySelector('.voice-dialog select').options.length,5);
document.querySelector('.voice-dialog .voice-controls button').click();
recognizers.at(-1).onresult({results:[Object.assign([{transcript:'Привет'}],{isFinal:true})]});await tick();
assert.equal(requests[0].data.voice,'speechkit:omazh');assert.equal(requests[0].data.text,'Привет!');assert.ok(audios[0].played);
audios[0].onended();await tick();assert.equal(requests[1].data.text,'Рада тебя слышать.');
audios[1].onended();await new Promise(resolve=>setTimeout(resolve,430));assert.equal(recognizers.length,2);assert.equal(released.length,2);
voice.stop();delayed=true;voice.speak('Тест отмены.');await tick();const request=requests.at(-1);voice.stop();assert.ok(request.options.signal.aborted);resolveAudio();await tick();assert.equal(audios.length,2);
// Changing a speaker produces a distinct provider ID, never a pitch alias.
delayed=false;const select=document.querySelector('.voice-dialog select');select.value='speechkit:jane';select.dispatchEvent(new window.Event('change'));voice.speak('Другой голос.');await tick();assert.equal(requests.at(-1).data.voice,'speechkit:jane');
voice.stop();assert.ok(audios.at(-1).paused);
// A local voice still uses the device engine.
select.value='device';select.dispatchEvent(new window.Event('change'));voice.speak('Локальный голос.');assert.equal(spoken.at(-1).voice.voiceURI,'device');voice.stop();
// Online speech remains usable on browsers without a device TTS engine.
window.speechSynthesis=null;localStorage.setItem('velia-voice-uri','speechkit:jane');
document.querySelector('#settings').replaceChildren();
const onlineOnly=setupVoice({send:async()=>{},busy:()=>false,toast:()=>{}});
onlineOnly.settings(document.querySelector('#settings'));await tick();
const soundButton=[...document.querySelector('#settings').querySelectorAll('button')].find(b=>b.textContent==='Послушать голос');
assert.equal(soundButton.disabled,false);soundButton.click();await tick();
assert.equal(requests.at(-1).data.voice,'speechkit:jane');onlineOnly.stop();
console.log(JSON.stringify({ok:true,synthetic:true,checks:['persisted remote voice','three catalog voices','phrase playback','emoji silent','microphone resumes','abort late download','speaker ID changes','device voice retained','online speech without device TTS']}));
onlineOnly.stop();localStorage.removeItem('velia-voice-uri');
const neural=['ru-RU-SvetlanaNeural','en-US-EmmaMultilingualNeural','en-US-AvaMultilingualNeural'].map((id,i)=>({voiceURI:'edge:'+id,name:['Светлана','Эмма','Ава'][i],lang:'ru-RU',gender:'female'}));
globalThis.fetch=async(url,options)=>{if(url.endsWith('/voices'))return {ok:true,json:async()=>({voices:neural})};requests.push({data:JSON.parse(options.body),options});return {ok:true,blob:async()=>new Blob(['fixture'])};};
const women=setupVoice({send:async()=>{},busy:()=>false,toast:()=>{}});
document.querySelector('#settings').replaceChildren();women.settings(document.querySelector('#settings'));await tick();
const femaleSelect=document.querySelector('#settings select');
for(const v of neural){femaleSelect.value=v.voiceURI;femaleSelect.dispatchEvent(new window.Event('change'));women.speak('Другой женский голос.');await tick();assert.equal(requests.at(-1).data.voice,v.voiceURI);women.stop();}
// A slow recognizer shutdown cannot delay the one request after final text.
window.speechSynthesis={getVoices:()=>[],addEventListener(){},speak(){},cancel(){}};
const delayedRecognition=[];window.SpeechRecognition=class{constructor(){delayedRecognition.push(this);}start(){}stop(){}abort(){}};
let submissions=0;const immediate=setupVoice({send:()=>{submissions++;return new Promise(()=>{});},busy:()=>false,toast:()=>{}});immediate.open();
const startButton=[...document.querySelectorAll('.voice-dialog')].at(-1).querySelector('.voice-controls button');startButton.click();
const recognizer=delayedRecognition.at(-1);recognizer.onresult({results:[Object.assign([{transcript:'Расскажи историю'}],{isFinal:true})]});
assert.equal(submissions,1);recognizer.onend();assert.equal(submissions,1);immediate.stop();women.stop();
console.log(JSON.stringify({ok:true,synthetic:true,checks:['three distinct female provider IDs','remote selection without device TTS','final recognition submits before delayed onend','late onend cannot duplicate request']}));
// At most one next phrase downloads while the current one downloads/plays.
const downloads=[];globalThis.fetch=async(url,options)=>{if(url.endsWith('/voices'))return {ok:true,json:async()=>({voices:neural})};return new Promise(resolve=>downloads.push({data:JSON.parse(options.body),signal:options.signal,resolve:()=>resolve({ok:true,blob:async()=>new Blob(['fixture'])})}));};
const audioStart=audios.length;let prefetched;
prefetched=setupVoice({send:async()=>{const text='Первая фраза. Вторая фраза. Третья фраза.';prefetched.update(text);prefetched.complete(text);},busy:()=>false,toast:()=>{}});prefetched.open();
[...document.querySelectorAll('.voice-dialog')].at(-1).querySelector('.voice-controls button').click();
delayedRecognition.at(-1).onresult({results:[Object.assign([{transcript:'Привет'}],{isFinal:true})]});await tick();
assert.deepEqual(downloads.map(x=>x.data.text),['Первая фраза.','Вторая фраза.']);
downloads[0].resolve();downloads[1].resolve();await tick();assert.equal(audios.length,audioStart+1);
audios.at(-1).onended();await tick();assert.equal(audios.length,audioStart+2);assert.equal(downloads[2].data.text,'Третья фраза.');
prefetched.stop();assert.ok(downloads[2].signal.aborted);downloads[2].resolve();await tick();assert.equal(audios.length,audioStart+2);
let specific;specific=setupVoice({send:async()=>specific.failure(false,'Quantum временно недоступна.'),busy:()=>false,toast:()=>{}});specific.open();
const errorDialog=[...document.querySelectorAll('.voice-dialog')].at(-1);errorDialog.querySelector('.voice-controls button').click();
delayedRecognition.at(-1).onresult({results:[Object.assign([{transcript:'Привет'}],{isFinal:true})]});await tick();
assert.equal(errorDialog.querySelector('.voice-status').textContent,'Quantum временно недоступна.');specific.stop();
console.log(JSON.stringify({ok:true,synthetic:true,checks:['one phrase prefetch without duplicate download','ready audio reused on phrase boundary','stop aborts unused prefetch','late prefetch cannot play','specific model error stays visible']}));
dom.window.close();
