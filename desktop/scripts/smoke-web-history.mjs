import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE || 'playwright');
const bundled=(await import(process.env.VELIA_CHROMIUM_MODULE || '@sparticuz/chromium')).default;
(async()=>{
const fixture=spawn('python',[fileURLToPath(new URL('./serve-web-fixture.py',import.meta.url))],{env:process.env,stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{fixture.kill();reject(new Error('Fixture startup timed out'));},15000);fixture.stdout.on('data',b=>{if(b.toString().includes('READY')){clearTimeout(timeout);resolve();}});fixture.once('exit',code=>reject(new Error('fixture '+code)));});
const browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE || await bundled.executablePath(),headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{
const context=await browser.newContext(),page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:18180/');await page.locator('#account').click();await page.locator('#pairing-code').fill('ABCDEFGH23456789');await page.locator('#auth-submit').click();await page.locator('#auth-dialog').waitFor({state:'hidden'});
const profile=await page.evaluate(async()=>await(await fetch('/web-api/v1/session')).json());
await page.evaluate(p=>localStorage.setItem('velia-web-chats-v1:'+p.account,JSON.stringify([{id:'local-test',title:'Private migration marker',updated:Date.now(),messages:[{role:'user',content:'PRIVATE_SECRET_MIGRATION'}]}])),profile);
// Remove the empty encrypted cache so the migration path can see legacy data.
await page.evaluate(async p=>{const {browserHistoryStore}=await import('/web/local-history.mjs');await browserHistoryStore().delete(p.account);},profile);
await page.reload();await page.getByText('Private migration marker',{exact:true}).waitFor();
assert.equal(await page.evaluate(p=>localStorage.getItem('velia-web-chats-v1:'+p.account),profile),null);
assert.equal(await page.evaluate(async p=>{const {browserHistoryStore}=await import('/web/local-history.mjs');const r=await browserHistoryStore().get(p.account);return new TextDecoder().decode(r.data).includes('PRIVATE_SECRET_MIGRATION');},profile),false);
await page.reload();await page.getByText('Private migration marker',{exact:true}).waitFor();
const other=await context.newPage();await other.goto('http://127.0.0.1:18180/');await other.getByText('Private migration marker',{exact:true}).waitFor();
await page.locator('#account').click();await page.locator('#logout-clear').check();await page.locator('#logout-confirm').click();
await page.waitForFunction(()=>document.querySelector('#account-label').textContent.startsWith('Войти'));
await other.waitForFunction(()=>document.querySelector('#account-label').textContent.startsWith('Войти'));
assert.equal(await other.getByText('Private migration marker',{exact:true}).count(),0);
assert.equal(await page.evaluate(async p=>{const {browserHistoryStore}=await import('/web/local-history.mjs');return (await browserHistoryStore().get(p.account))===undefined;},profile),true);
assert.deepEqual(errors,[]);
console.log('SECURE_HISTORY_BROWSER_ACCEPTED migration ciphertext reload cross-tab-logout deletion');
}finally{await browser.close();fixture.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
