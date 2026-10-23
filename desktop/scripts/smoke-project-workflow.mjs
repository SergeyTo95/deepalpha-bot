/** Browser acceptance with synthetic account/project/model data; no live provider calls. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const bundled=(await import(process.env.VELIA_CHROMIUM_MODULE||'@sparticuz/chromium')).default;
const output=process.argv[2]||'/tmp/velia-project-workflow-qa';await fs.mkdir(output,{recursive:true});
const fixture=spawn(process.env.VELIA_TEST_PYTHON||'python3',['desktop/scripts/serve-web-fixture.py'],{stdio:['ignore','pipe','pipe']});
let fixtureErrors='';fixture.stderr.on('data',value=>fixtureErrors+=value);
await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(new Error('Fixture timeout: '+fixtureErrors)),20000);
  fixture.stdout.on('data',value=>{if(String(value).includes('VELIA_WEB_FIXTURE_READY')){clearTimeout(timer);resolve();}});
  fixture.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code+': '+fixtureErrors));});
});
let browser,page;
try{
  browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE||await bundled.executablePath(),
    args:bundled.args.filter(a=>!['--disable-web-security','--allow-running-insecure-content'].includes(a)),headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:960}});page=await context.newPage();
  const errors=[],requests=[],completed=new Set(),messages=[];
  page.on('pageerror',error=>errors.push(error.message));
  const project='11111111-1111-4111-8111-111111111111',resource='22222222-2222-4222-8222-222222222222';
  let created=false,seed='';
  const stages={recon:'Разбор сервиса',feedback:'Анализ отзывов',test:'План проверок',diff:'Оценка готовности'};
  let title='Проект <img src=x onerror="window.untrustedExecuted=1">';
  await context.route('**/web-api/v1/features/**',async route=>{
    const path=new URL(route.request().url()).pathname.split('/features/')[1];
    const item={id:resource,title:'Разбор: example.com',project_id:project,kind:'chat',seed_query:seed};
    const result=path==='projects'?{projects:[{id:project,passport:{title,goal:'Запись клиентов'}}]}:
      path==='projects/'+project?{project:{id:project,revision:1,passport:{title,goal:'Запись клиентов'}}}:
      path==='project-resources'?{resources:created?[item]:[]}:{};
    await route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,...result})});
  });
  await context.route('**/web-api/v1/project-workflows**',async route=>{
    if(route.request().method()==='POST'){
      const data=route.request().postDataJSON();assert.equal(data.project_id,project);assert.ok(route.request().headers()['idempotency-key']);
      seed='VELIA_PROJECT_WORKFLOW_V1\n'+JSON.stringify({version:'velia-replica-v1',url:data.url,goal:data.goal});created=true;
      await route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({ok:true,resource:{id:resource,title:'Разбор: example.com'}})});
    }else await route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,stages,workflow:{goal:'Удобная запись',completed:[...completed]}})});
  });
  await context.route('**/web-api/v1/conversations**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    if(path.endsWith('/messages/stream')){
      const data=request.postDataJSON();requests.push(data);assert.equal(data.model,'velia-flash');assert.equal(data.project_stage.project_id,project);
      const stage=data.project_stage.stage;completed.add(stage);
      const content='Готово: '+stage+(stage==='diff'?'\n\nРасчёт по внесённым результатам: 0%. Есть блокирующие пробелы.':'');
      messages.push({role:'user',content:data.content,project_stage:data.project_stage,status:'completed',chat_mode:'flash'},
        {role:'assistant',content,status:'completed',chat_mode:'flash'});
      await route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify({choices:[{delta:{content},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n'});
    }else await route.fulfill({contentType:'application/json',body:JSON.stringify(path.endsWith('/messages')?{ok:true,messages}:
      {ok:true,conversations:created?[{id:resource,title:'Разбор: example.com',updated_at:new Date().toISOString()}]:[]})});
  });
  await page.goto('http://127.0.0.1:18180/');
  await page.locator('#account').click();await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');await page.locator('#auth-submit').click();
  await page.locator('#auth-dialog').waitFor({state:'hidden'});
  async function openProject(){
    if((await page.viewportSize()).width<=900)await page.locator('#menu').click();
    await page.locator('#feature-nav').getByRole('button',{name:'Проекты',exact:true}).click();
    await page.getByRole('button',{name:'Открыть',exact:true}).click();
    await page.getByRole('button',{name:'Начать разбор',exact:true}).waitFor();
  }
  async function openWorkflow(){await openProject();await page.getByRole('button',{name:'Открыть',exact:true}).last().click();await page.getByLabel('Этап',{exact:true}).waitFor();}
  await openProject();assert.equal(await page.evaluate(()=>window.untrustedExecuted),undefined);
  title='Онлайн-запись клиентов';
  await page.getByLabel('Ссылка на сервис',{exact:true}).fill('https://example.com/product');
  await page.getByLabel('Что хочешь создать или улучшить',{exact:true}).fill('Удобная запись');
  await page.getByRole('button',{name:'Начать разбор',exact:true}).click();
  await page.locator('#messages').getByText('Готово: recon',{exact:true}).waitFor();
  await openWorkflow();await page.getByLabel('Этап',{exact:true}).selectOption('diff');
  assert.equal(await page.getByRole('button',{name:'Выполнить этап',exact:true}).isDisabled(),true);
  for(const [stage,notes] of [['feedback','https://reviews.example/a | Медленно и вылетает'],['test','Проверь изоляцию аккаунтов'],
    ['diff','Login | must | failed | https://tests.example/login']]){
    if(stage!=='feedback')await openWorkflow();
    await page.getByLabel('Этап',{exact:true}).selectOption(stage);
    if(stage==='feedback'){
      await page.getByLabel('Ссылка на отзыв',{exact:true}).fill('https://reviews.example/a');await page.getByLabel('Текст отзыва',{exact:true}).fill('Медленно и вылетает');
    }else if(stage==='diff'){
      await page.getByLabel('Что проверяли',{exact:true}).fill('Login');await page.getByLabel('Результат',{exact:true}).selectOption('failed');await page.getByLabel('Ссылка на доказательство',{exact:true}).fill('https://tests.example/login');
    }else await page.getByLabel('Материалы и замечания',{exact:true}).fill(notes);
    await page.getByRole('button',{name:'Выполнить этап',exact:true}).click();
    await page.locator('#messages').getByText('Готово: '+stage,{exact:false}).waitFor();
  }
  assert.deepEqual(requests.map(value=>value.project_stage.stage),Object.keys(stages));
  assert.ok(requests.every(value=>value.web_search===false));
  await page.reload();await openWorkflow();
  assert.equal(await page.getByLabel('Этап',{exact:true}).locator('option').filter({hasText:'ответ сохранён'}).count(),4);
  await page.getByLabel('Этап',{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:output+'/projects-desktop.png',fullPage:true});
  for(const width of [390,360]){
    await page.setViewportSize({width,height:844});
    await page.waitForTimeout(400);await page.getByLabel('Этап',{exact:true}).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const bounds=await page.getByLabel('Этап',{exact:true}).boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width);
    await page.screenshot({path:output+`/projects-${width}.png`,fullPage:true});
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,stages:requests.length,resumed:true,widths:[1440,390,360],pageErrors:errors}));
}catch(error){
  if(page){await page.screenshot({path:output+'/failure.png',fullPage:true}).catch(()=>{});console.error((await page.locator('body').innerText()).slice(-8000));}
  throw error;
}finally{await browser?.close();fixture.kill('SIGTERM');}
