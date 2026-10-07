/** Synthetic browser speech lifecycle checks. Does not qualify real microphones/providers. */
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const output=process.argv[2]||'/tmp/velia-voice-qa';await fs.mkdir(output,{recursive:true});
const server=createServer(async(req,res)=>{if(req.url==='/voice.mjs'||req.url==='/style.css'){res.setHeader('Content-Type',req.url.endsWith('mjs')?'text/javascript':'text/css');res.end(await fs.readFile('desktop/web'+req.url));}else res.end('<html><head><link rel="stylesheet" href="/style.css"></head><body><section id="settings"></section></body></html>');});
await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
try{
 browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage'],headless:true});
 const page=await browser.newPage({viewport:{width:393,height:710}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.evaluate(async()=>{
  window.recognizers=[];window.spoken=[];window.sends=[];
  window.SpeechRecognition=class {constructor(){window.recognizers.push(this);}start(){}abort(){this.onend?.();}};
  window.SpeechSynthesisUtterance=class {constructor(text){this.text=text;}};
  Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},getVoices(){return [{name:'Тестовый голос',voiceURI:'test',lang:'ru-RU',localService:true}];},addEventListener(){},speak(u){window.spoken.push(u);}}});
  const {setupVoice}=await import('/voice.mjs');window.voice=setupVoice({busy:()=>false,toast:()=>{},send:async text=>{window.sends.push(text);window.voice.complete('Это ответ. '.repeat(80));}});window.voice.settings(document.querySelector('#settings'));window.voice.open();
 });
 await page.getByRole('button',{name:'Начать',exact:true}).click();
 await page.evaluate(()=>{const r=recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Промежуточная речь'}],{isFinal:false})]});});
 assert.match(await page.locator('.voice-captions').innerText(),/Промежуточная речь/);
 await page.evaluate(()=>{const r=recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Привет Велия'}],{isFinal:true})]});r.onend();});
 await page.waitForFunction(()=>spoken.length===1);assert.deepEqual(await page.evaluate(()=>sends),['Привет Велия']);
 await page.screenshot({path:output+'/voice-mobile-speaking.png'});
 await page.evaluate(()=>{for(let i=0;i<30;i++){const u=spoken.at(-1);if(u===window.last)break;window.last=u;u.onend();}});
 await page.waitForFunction(()=>recognizers.length===2);
 assert.ok(await page.evaluate(()=>spoken.length>1&&spoken.every(u=>u.text.length<=240)));
 // A silence timeout resumes listening without generating a message.
 await page.evaluate(()=>{recognizers.at(-1).onerror({error:'no-speech'});recognizers.at(-1).onend();});
 await page.waitForFunction(()=>recognizers.length===3);assert.equal(await page.evaluate(()=>sends.length),1);
 // Stale callbacks after pause cannot send or restart microphone.
 await page.getByRole('button',{name:'Пауза',exact:true}).click();
 await page.evaluate(()=>{const r=recognizers.at(-1);r.onresult({results:[Object.assign([{transcript:'Нельзя отправить'}],{isFinal:true})]});r.onend();});
 await page.waitForTimeout(900);assert.equal(await page.evaluate(()=>recognizers.length),3);assert.equal(await page.evaluate(()=>sends.length),1);
 await page.getByRole('button',{name:'Начать',exact:true}).click();await page.evaluate(()=>recognizers.at(-1).onerror({error:'not-allowed'}));
 assert.match(await page.locator('.voice-status').innerText(),/Разреши микрофон/);
 await page.getByRole('button',{name:'Завершить',exact:true}).click();await page.getByLabel('Голос',{exact:true}).selectOption('test');await page.getByLabel('Скорость речи').selectOption('1.2');await page.getByRole('button',{name:'Послушать голос'}).click();
 assert.equal(await page.evaluate(()=>spoken.at(-1).rate),1.2);assert.equal(await page.evaluate(()=>spoken.at(-1).voice.voiceURI),'test');
 await page.evaluate(()=>{voice.open();});await page.getByRole('button',{name:'Начать',exact:true}).click();await page.evaluate(()=>{voice.stop();voice.open();});
 for(const width of [360,393,768,1440]){await page.setViewportSize({width,height:710});const box=await page.locator('.voice-dialog').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);}
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,synthetic:true,checks:['captions','multi-chunk reply','silence retry','stale callback safety','permission error','voice/rate','mobile layout']}));
}finally{await browser?.close();server.close();}
