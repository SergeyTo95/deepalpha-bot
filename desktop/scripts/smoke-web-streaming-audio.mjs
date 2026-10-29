/** Real Chromium MP3 decoding with a synthetic tone, not a live speech service. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const mp3=execFileSync('ffmpeg',['-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-ar','24000','-b:a','48k','-f','mp3','pipe:1']);
const jobs=[];
const gate=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const server=createServer(async(req,res)=>{
 const path=new URL(req.url,'http://localhost').pathname;
 if(['/voice.mjs','/speech-audio.mjs'].includes(path)){
  res.setHeader('Content-Type','text/javascript');res.end(await fs.readFile('desktop/web'+path));return;
 }
 if(path.endsWith('/voices')){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({voices:[{voiceURI:'edge:en-US-AvaMultilingualNeural',name:'Ава',lang:'ru-RU'}]}));return;}
 if(path.endsWith('/speech')){
  const chunks=[];for await(const chunk of req)chunks.push(chunk);
  const job={url:req.url,data:JSON.parse(Buffer.concat(chunks)),first:gate(),tail:gate(),ended:false};jobs.push(job);
  res.once('close',()=>{job.first.resolve();job.tail.resolve();});
  await job.first.promise;
  res.writeHead(200,{'Content-Type':'audio/mpeg','X-Velia-Audio-Stream':'1'});
  if(req.url.includes('stream=1')){res.write(mp3.subarray(0,8192));await job.tail.promise;res.end(mp3.subarray(8192));}
  else res.end(mp3);
  job.ended=true;return;
 }
 res.setHeader('Content-Type','text/html');res.end('<html lang="ru"><button id="start">Start</button></html>');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
try{
 browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--mute-audio']});
 for(const streaming of [true,false]){
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.evaluate(async streaming=>{
   if(!streaming)Object.defineProperty(window,'MediaSource',{value:undefined});
   window.recognizers=[];window.audios=[];
   window.SpeechRecognition=class{constructor(){recognizers.push(this);}start(){}stop(){this.onend?.();}abort(){this.onend?.();}};
   Object.defineProperty(window,'speechSynthesis',{value:null});
   const Audio=window.Audio;window.Audio=function(){const audio=new Audio();audios.push(audio);return audio;};
   localStorage.setItem('velia-voice-language','ru-RU');localStorage.setItem('velia-voice-uri','edge:en-US-AvaMultilingualNeural');
   const {setupVoice}=await import('/voice.mjs');
   window.voice=setupVoice({busy:()=>false,toast:()=>{},send:async()=>voice.complete('Проверка голоса.')});
   document.querySelector('#start').onclick=()=>voice.open();
  },streaming);
  await page.click('#start');
  await page.evaluate(()=>{const r=recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Проверка'}],{isFinal:true})]});r.onend();});
  const job=await new Promise((resolve,reject)=>{const until=Date.now()+5000;const poll=()=>{if(jobs.length)resolve(jobs.shift());else if(Date.now()>until)reject(Error('missing speech request'));else setTimeout(poll,10);};poll();});
  assert.equal(job.url.includes('stream=1'),streaming);assert.equal(job.data.voice,'edge:en-US-AvaMultilingualNeural');
  assert.equal(await page.locator('.voice-status').textContent(),'Готовлю голос…');
  assert.equal(await page.locator('.voice-dialog').getAttribute('data-phase'),'thinking');
  job.first.resolve();
  await page.waitForFunction(()=>document.querySelector('.voice-dialog').dataset.phase==='speaking');
  await page.waitForFunction(()=>audios.at(-1).currentTime>0);
  if(streaming)assert.equal(job.ended,false,'playback waited for the entire provider response');
  job.tail.resolve();await page.waitForFunction(()=>recognizers.length===2);
  assert.deepEqual(errors,[]);await page.evaluate(()=>voice.stop());await page.close();
 }
 console.log(JSON.stringify({ok:true,real_chromium:true,synthetic_audio:true,checks:['download status is preparing','actual MP3 playback before provider EOF','speaking follows playing event','same selected female voice','microphone resumes after audio ends','Blob compatibility without MediaSource']}));
}finally{await browser?.close();server.closeAllConnections();server.close();}
