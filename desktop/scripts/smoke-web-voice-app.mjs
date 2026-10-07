/** Full web app voice routing with synthetic speech hardware and fixture account. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const output=process.argv[2]||'/tmp/velia-voice-app-qa';await fs.mkdir(output,{recursive:true});
const fixture=spawn('python',['desktop/scripts/serve-web-fixture.py'],{stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture timeout')),15000);fixture.stdout.on('data',b=>{if(String(b).includes('VELIA_WEB_FIXTURE_READY')){clearTimeout(timer);resolve();}});fixture.on('exit',c=>reject(new Error('Fixture exit '+c)));});
let browser;
try{
 browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage'],headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),calls=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/web-api/'))calls.push({path:new URL(r.url()).pathname,data:r.postDataJSON()});});
 await context.addInitScript(()=>{
  window.recognizers=[];window.spoken=[];window.unlocks=0;
  window.SpeechRecognition=class{constructor(){recognizers.push(this);}start(){}abort(){this.onend?.();}stop(){this.onend?.();}};
  window.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
  Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},resume(){},getVoices(){return[{name:'Fixture RU',voiceURI:'fixture-ru',lang:'ru-RU',localService:true}];},addEventListener(){},speak(u){if(u.volume===0){unlocks++;return;}spoken.push(u);}}});
 });
 await context.route('**/web-api/v1/auth/exchange',async route=>{const response=await route.fetch(),data=await response.json();await route.fulfill({response,json:{...data,browser_agent:true}});});
 await page.goto('http://127.0.0.1:18180/');
 await page.locator('#account').click();await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');await page.locator('#auth-submit').click();await page.locator('#auth-dialog').waitFor({state:'hidden'});
 await page.locator('#agent-toggle').click();assert.equal(await page.locator('#agent-toggle').getAttribute('aria-pressed'),'true');
 await page.setViewportSize({width:393,height:710});await page.locator('#voice-open').click();
 assert.equal(await page.locator('.voice-dialog [aria-label="Профиль голоса"] option').count(),7);
 await page.locator('.voice-dialog').getByLabel('Профиль голоса').selectOption('Nora');
 await page.getByRole('button',{name:'Начать',exact:true}).click();
 await page.evaluate(()=>recognizers.at(-1).onresult({results:[Object.assign([{transcript:'Велия, привет'}],{isFinal:true})]}));
 await page.waitForFunction(()=>spoken.length>0);
 assert.equal(await page.locator('#agent-toggle').getAttribute('aria-pressed'),'false');
 const message=calls.find(c=>c.path.endsWith('/messages/stream'));assert.ok(message);assert.equal(message.data.voice_turn,true);assert.equal(message.data.web_search,false);assert.equal(message.data.model,'velia-flash');
 assert.ok(!calls.some(c=>c.path.includes('/agent/browser')));
 assert.ok(await page.evaluate(()=>spoken.at(-1).text.includes('Я Велия')));
 assert.equal(await page.evaluate(()=>spoken.at(-1).pitch),1.10);
 assert.ok(await page.evaluate(()=>unlocks>=1));
 await page.screenshot({path:output+'/voice-reply.png'});
 await page.evaluate(()=>spoken.at(-1).onend());await page.waitForFunction(()=>recognizers.length===2);
 // A second turn uses the same ordinary account conversation.
 await page.evaluate(()=>recognizers.at(-1).onresult({results:[Object.assign([{transcript:'Как дела?'}],{isFinal:true})]}));
 await page.waitForFunction(()=>spoken.length===2);
 const messages=calls.filter(c=>c.path.endsWith('/messages/stream'));assert.equal(messages.length,2);assert.equal(messages[0].path,messages[1].path);
 await page.getByRole('button',{name:'Завершить',exact:true}).click();assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,synthetic:true,checks:['Agent ON to voice Flash','account voice fast flag','no browser task','7 Android profiles','TTS output with selected profile','microphone resumes','same chat second turn']}));
}finally{await browser?.close();fixture.kill();}
