/** Full web app voice routing with synthetic speech hardware and fixture account. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const selected=process.env.VELIA_TEST_CHAT_MODEL||'velia-flash';
const output=process.argv[2]||'/tmp/velia-voice-app-qa';await fs.mkdir(output,{recursive:true});
const fixture=spawn('python',['desktop/scripts/serve-web-fixture.py'],{env:{...process.env,VELIA_FIXTURE_QUANTUM:'true'},stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture timeout')),15000);fixture.stdout.on('data',b=>{if(String(b).includes('VELIA_WEB_FIXTURE_READY')){clearTimeout(timer);resolve();}});fixture.on('exit',c=>reject(new Error('Fixture exit '+c)));});
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage'],headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),calls=[],uploads=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/web-api/')){if(r.headers()['content-type']?.includes('multipart/form-data'))uploads.push({path:new URL(r.url()).pathname,mode:r.headers()['x-velia-chat-mode']});else calls.push({path:new URL(r.url()).pathname,data:r.postDataJSON()});}});
 await context.addInitScript(()=>{
  localStorage.setItem('velia-voice-language','ru-RU');window.recognizers=[];window.spoken=[];window.unlocks=0;
  window.SpeechRecognition=class{constructor(){recognizers.push(this);}start(){}abort(){this.onend?.();}stop(){this.onend?.();}};
  window.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
  Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},resume(){},getVoices(){return[{name:'Fixture RU',voiceURI:'fixture-ru',lang:'ru-RU',localService:true}];},addEventListener(){},speak(u){if(u.volume===0){unlocks++;return;}if(!spoken.length)window.firstSpeechBeforeComplete=document.querySelector('#prompt').disabled;spoken.push(u);}}});
 });
 await context.route('**/web-api/v1/auth/exchange',async route=>{const response=await route.fetch(),data=await response.json();await route.fulfill({response,json:{...data,browser_agent:true}});});
 await page.goto('http://127.0.0.1:18180/');
 await page.locator('#account').click();await page.locator('#code-ready').click();await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');await page.locator('#auth-submit').click();await page.locator('#auth-dialog').waitFor({state:'hidden'});
 if(selected==='velia-quantum'){await page.locator('#model-button').click();await page.locator('[data-model="velia-quantum"]').click();}
 await page.locator('#agent-toggle').click();assert.equal(await page.locator('#agent-toggle').getAttribute('aria-pressed'),selected==='velia-flash'?'true':'false');
 assert.equal(await page.locator('#model-label').innerText(),selected==='velia-quantum'?'VELIA QUANTUM':'VELIA FLASH');
 await page.setViewportSize({width:393,height:710});await page.locator('#voice-open').click();
 assert.equal(await page.locator('.voice-dialog [aria-label="Голос"] option').count(),2);
 await page.locator('.voice-dialog').getByLabel('Голос',{exact:true}).selectOption('fixture-ru');
 await page.getByRole('button',{name:'Начать',exact:true}).click();
 await page.evaluate(()=>recognizers.at(-1).onresult({results:[Object.assign([{transcript:'Велия, привет'}],{isFinal:true})]}));
 await page.waitForFunction(()=>spoken.length>0);
 assert.equal(await page.locator('#agent-toggle').getAttribute('aria-pressed'),'false');
 const message=calls.find(c=>c.path.endsWith('/messages/stream'));assert.ok(message);assert.equal(message.data.voice_turn,true);assert.equal(message.data.web_search,true);assert.equal(message.data.model,selected);
 assert.ok(!calls.some(c=>c.path.includes('/agent/browser')));
 assert.ok(await page.evaluate(()=>spoken.at(-1).text.includes('Я Велия')));
 assert.equal(await page.evaluate(()=>spoken.at(-1).voice.voiceURI),'fixture-ru');
 assert.ok(await page.evaluate(()=>unlocks>=1));
 await page.screenshot({path:output+'/voice-reply.png'});
 assert.equal(await page.evaluate(()=>firstSpeechBeforeComplete),true);await page.waitForFunction(()=>!document.querySelector('#prompt').disabled);await page.evaluate(()=>{for(let i=0;i<30;i++){const u=spoken.at(-1);if(u===window.lastSpoken)break;window.lastSpoken=u;u.onend();}window.firstSpoken=spoken.length;});await page.waitForFunction(()=>recognizers.length===2);
 // A second turn uses the same ordinary account conversation.
 await page.evaluate(()=>recognizers.at(-1).onresult({results:[Object.assign([{transcript:'Как дела?'}],{isFinal:true})]}));
 await page.waitForFunction(()=>spoken.length>firstSpoken);
 const messages=calls.filter(c=>c.path.endsWith('/messages/stream'));assert.equal(messages.length,2);assert.equal(messages[0].path,messages[1].path);
 await page.getByRole('button',{name:'Завершить',exact:true}).click();
 await page.setViewportSize({width:1440,height:960});
 await page.locator('#new-chat').click();
 await page.setViewportSize({width:393,height:710});
 await page.locator('#attachment-input').setInputFiles({name:'invoice.txt',mimeType:'text/plain',buffer:Buffer.from('Invoice 42 TON')});
 await page.locator('#prompt').fill('Перескажи документ.');await page.locator('#send').click();
 await page.locator('.message-actions').last().waitFor();
 assert.equal(uploads.at(-1).mode,selected.replace('velia-',''));
 assert.deepEqual((await (await fetch('http://127.0.0.1:18181/__fixture/uploads')).json()).modes,[selected.replace('velia-','')]);
 assert.equal(calls.filter(c=>c.path.endsWith('/messages/stream')).at(-1).data.model,selected);
 assert.equal(calls.filter(c=>c.path.endsWith('/messages/stream')).at(-1).data.attachment_ids.length,1);
 assert.ok((await page.locator('.assistant .message-content').last().innerText()).includes('Я Велия'));
 await page.reload();await page.locator('.message-files').filter({hasText:'invoice.txt'}).waitFor();
 assert.equal(await page.locator('#model-label').innerText(),selected==='velia-quantum'?'VELIA QUANTUM':'VELIA FLASH');
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,model:selected,synthetic:true,checks:['selected voice model preserved','account voice fast flag','no browser task','real device voice','TTS output with selected profile','microphone resumes','same chat second turn','selected file upload and answer','file metadata after reload']}));
}finally{await browser?.close();fixture.kill();}
