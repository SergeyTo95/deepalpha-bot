/** Real browser acceptance, driven only against the local synthetic HTTP fixture. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const bundled=(await import(process.env.VELIA_CHROMIUM_MODULE||'@sparticuz/chromium')).default;
const root=process.argv[2]||'/tmp/velia-web-qa';await fs.mkdir(root,{recursive:true});
const fixture=spawn('python3',[fileURLToPath(new URL('./serve-web-fixture.py',import.meta.url))],{stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(new Error('Fixture startup timed out')),15000);fixture.stdout.on('data',chunk=>{output+=chunk;if(output.includes('VELIA_WEB_FIXTURE_READY')){clearTimeout(timeout);resolve();}});fixture.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code));});});
const browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE||await bundled.executablePath(),args:bundled.args.filter(a=>a!=='--disable-web-security'&&a!=='--allow-running-insecure-content'),headless:true});
try{
  const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[],calls=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error'&&!m.text().includes('401'))errors.push(m.text());});
  page.on('request',r=>{if(r.url().includes('/chat/completions'))calls.push(r.postDataJSON());});
  await page.goto('http://127.0.0.1:18180/');await page.locator('#welcome').waitFor();
  await page.screenshot({path:root+'/VELIA-Web-desktop.png'});
  await page.locator('#model-button').click();await page.getByRole('option',{name:/VELIA FLASH/}).click();
  await page.reload();await page.locator('#model-label').filter({hasText:'VELIA FLASH'}).waitFor();
  assert.equal(calls.length,0);
  await page.locator('#prompt').fill('Привет, Велия');await page.locator('#send').click();await page.locator('#auth-dialog').waitFor({state:'visible'});
  await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');await page.locator('#auth-submit').click();await page.locator('#auth-dialog').waitFor({state:'hidden',timeout:8000}).catch(async error=>{console.log('VELIA_WEB_FIXTURE_LOGIN_FAILURE',JSON.stringify({error:await page.locator('#auth-error').innerText(),browserErrors:errors}));throw error;});
  // The draft survives login, and the first answer uses the selected mode.
  const sessionCookie=(await context.cookies()).find(c=>c.name==='__Host-velia-web');
  assert.ok(sessionCookie?.httpOnly&&sessionCookie.secure&&sessionCookie.sameSite==='Lax');
  assert.equal(await page.locator('#prompt').inputValue(),'Привет, Велия');
  await page.locator('#send').click();await page.locator('.message-actions').waitFor();
  assert.equal(calls.at(-1).model,'velia-flash');assert.ok((await page.locator('.assistant').innerText()).includes('Готова помочь'));
  assert.equal(await page.locator('.code-block').count(),1);
  await page.reload();await page.locator('.assistant .message-actions').waitFor();assert.ok((await page.locator('#messages').innerText()).includes('Привет, Велия'));
  await page.locator('#model-button').click();await page.getByRole('option',{name:/VELIA PRO/}).click();await page.locator('#prompt').fill('Продолжи');await page.locator('#send').click();await page.locator('.message-actions').last().waitFor();
  assert.equal(calls.at(-1).model,'velia-pro');assert.equal(calls.at(-1).messages.length,3);
  await page.screenshot({path:root+'/VELIA-Web-chat.png'});
  await page.locator('#prompt').fill('Покажи остановку');await page.locator('#send').click();await page.locator('#stop').waitFor({state:'visible'});await page.locator('#stop').click();await page.getByText('Ответ остановлен.',{exact:true}).waitFor();
  await page.locator('#new-chat').click();await page.locator('#search').fill('Привет');assert.equal(await page.locator('.history-row').count(),1);await page.locator('.history-delete').click();await page.locator('#delete-confirm').click();assert.equal(await page.locator('.history-row').count(),0);await page.locator('#search').fill('');
  await page.locator('#theme').click();assert.equal(await page.locator('html').getAttribute('data-theme'),'light');await page.reload();assert.equal(await page.locator('html').getAttribute('data-theme'),'light');
  await page.screenshot({path:root+'/VELIA-Web-light.png'});
  await page.locator('#account').click();await page.getByText('Войти в VELIA',{exact:true}).first().waitFor();assert.equal(await page.locator('.history-row').count(),0);
  assert.equal((await context.cookies()).filter(c=>c.name==='__Host-velia-web').length,0);
  const mobile=await context.newPage();await mobile.setViewportSize({width:390,height:844});await mobile.goto('http://127.0.0.1:18180/');await mobile.locator('#welcome').waitFor();
  assert.ok(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await mobile.locator('#theme').click();await mobile.screenshot({path:root+'/VELIA-Web-mobile.png'});
  await mobile.locator('#menu').click();await mobile.locator('#sidebar.open').waitFor();await mobile.locator('#scrim').click({position:{x:350,y:250}});await mobile.locator('#sidebar.open').waitFor({state:'hidden'});
  await mobile.locator('#model-button').click();await mobile.getByRole('option',{name:/VELIA FLASH/}).click();assert.equal(await mobile.locator('#model-label').innerText(),'VELIA FLASH');
  assert.deepEqual(errors,[]);
  console.log('VELIA_WEB_BROWSER_QUALIFIED',JSON.stringify({flash:true,pro:true,history:true,login:true,logout:true,cookieHttpOnly:true,stop:true,mobile:true,themes:true,modelCalls:calls.length,fixture:true,liveModel:false}));
}finally{await browser.close();fixture.kill();}
