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
console.log(JSON.stringify({ok:true,synthetic:true,checks:['persisted remote voice','three catalog voices','phrase playback','emoji silent','microphone resumes','abort late download','speaker ID changes','device voice retained']}));
dom.window.close();
