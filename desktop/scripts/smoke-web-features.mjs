/** Browser UI acceptance with synthetic feature responses; no real account or media calls. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.VELIA_PLAYWRIGHT_MODULE||'playwright');
const bundled=(await import(process.env.VELIA_CHROMIUM_MODULE||'@sparticuz/chromium')).default;
const output=process.argv[2]||'/tmp/velia-feature-qa';await fs.mkdir(output,{recursive:true});
const fixture=spawn('python',['desktop/scripts/serve-web-fixture.py'],{stdio:['ignore','pipe','pipe']});
await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Fixture timeout')),15000);fixture.stdout.on('data',b=>{if(String(b).includes('VELIA_WEB_FIXTURE_READY')){clearTimeout(timer);resolve();}});fixture.on('exit',c=>reject(new Error('Fixture exit '+c)));});
let browser;
try{
  browser=await chromium.launch({executablePath:process.env.VELIA_CHROMIUM_EXECUTABLE||await bundled.executablePath(),args:bundled.args.filter(a=>!['--disable-web-security','--allow-running-insecure-content'].includes(a)),headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:960}}),page=await context.newPage(),errors=[],calls=[];
  page.on('pageerror',e=>errors.push(e.message));
  let scheduleItems=[],schedulerEnabled=true,jobStatus='completed';
  const id='11111111-1111-1111-1111-111111111111';
  await context.route('**/web-api/v1/features/**',async route=>{
    const req=route.request(),p=new URL(req.url()).pathname.split('/features/')[1];calls.push({method:req.method(),path:p,body:req.headers()['content-type']?.includes('json')?req.postDataJSON():null});
    const map={
      profile:{profile:{preferred_name:'Сергей',about_me:'Проекты'}},
      plugins:{plugins:{weather:{enabled:true,available:true},web_search:{enabled:true,available:true}}},
      'economy/me':{account:{credits:100}},usage:{usage:{user_messages:2}},
      projects:{projects:[{id,revision:1,passport:{title:'Мой проект',goal:'Цель'}}]},
      ['projects/'+id]:{project:{id,revision:1,passport:{title:'Мой проект',goal:'Цель'}}},
      'project-resources':{resources:[]},'research/missions':{missions:[{id,goal:'Тема исследования'}]},
      ['research/missions/'+id]:{mission:{id,goal:'Тема исследования',status:'planned'}},
      'medical/cases':{cases:[{id,title:'КТ',status:'draft'}]},['medical/cases/'+id]:{case:{id,title:'КТ',status:'draft'}},
      agents:{agents:[{id,name:'Мой агент'}]},'agents/capabilities':{capabilities:[{id:'research',name:'Исследования'}]},
      'agent/status':{enabled:true,tools:[{name:'velia.tasks.list',enabled:true},{name:'velia.tasks.create_draft',enabled:true}]},'agent/schedules/status':{enabled:true},'agent/schedules':{schedules:[]},'developer/autopilot/status':{worker_ready:true},
      'developer/projects':{projects:[{id:'repo1',repository_full_name:'owner/repo'}]},
      'developer/autopilot/missions':{missions:[{mission_id:'mission1',name:'Миссия',status:'paused'}]},
      'developer/autopilot/missions/mission1/tasks':{tasks:[]},
      'studio/status':{enabled:true,image:{providers:[{id:'velia_image',label:'Velia Image',enabled:true},{id:'velia_image_2',label:'Velia Image 2',enabled:true}]},video:{duration_options_seconds:[5,10,15]},music:{enabled:true,duration_options_seconds:[30,60]}},
      'studio/sessions':{sessions:[{id,title:'Моя Studio'}]},['studio/sessions/'+id+'/messages']:{messages:[{content:'Готово',generation:{type:'image',media:{id:'image1',content_url:'/api/mobile/images/image1/content?user_id=7&expires=123&signature=abc'}}}]},
    };
    let result=map[p]||{};
    if(p==='agent/schedules/status')result={enabled:schedulerEnabled};
    if(p==='agent/schedules'){
      if(req.method()==='POST'){const d=req.postDataJSON();scheduleItems.push({...d,schedule_id:'schedule1',enabled:false,last_job_id:'job1'});result={schedule:scheduleItems.at(-1)};}
      else result={schedules:scheduleItems};
    }
    if(p==='agent/schedules/schedule1')result={schedule:scheduleItems[0]};
    if(p==='agent/schedules/schedule1/enable')scheduleItems[0].enabled=true;
    if(p==='agent/schedules/schedule1/disable')scheduleItems[0].enabled=false;
    if(p==='agent/jobs/job1/actions/action1/approve')jobStatus='planned';
    if(p==='agent/jobs/job1/run')jobStatus='completed';
    if(p==='agent/jobs/job1')result={job:{status:jobStatus,actions:[{action_id:'action1',status:jobStatus==='awaiting_approval'?'awaiting_approval':jobStatus,result:{items:[{title:'Проверить проект'}]}}]}};

    if(req.method()==='POST'&&p==='studio/sessions')result={session:{id,title:'Новая Studio'}};
    if(req.method()==='POST'&&p.endsWith('/assets'))result={asset:{id:'ref1'}};
    if(req.method()==='POST'&&p.endsWith('/attachments'))result={attachment:{id}};
    if(p.startsWith('media/'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPmQAAAAASUVORK5CYII=','base64')});
    await route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,...result})});
  });
  await context.route('**/web-api/v1/auth/exchange',async route=>{const response=await route.fetch(),data=await response.json();await route.fulfill({response,json:{...data,browser_agent:true}});});
  await page.goto('http://127.0.0.1:18180/');
  await page.locator('#model-button').click();assert.equal(await page.locator('[data-model="velia-quantum"]').isDisabled(),true);assert.match(await page.locator('[data-model="velia-quantum"]').innerText(),/Скоро/);await page.locator('#model-button').click();
  await page.locator('#account').click();await page.locator('#pairing-code').fill('ABCD-EFGH-2345-6789');await page.locator('#auth-submit').click();await page.locator('#auth-dialog').waitFor({state:'hidden'});
  const open=async name=>{await page.locator('#feature-nav').getByRole('button',{name,exact:true}).click();await page.locator('#feature-view h1').getByText(name,{exact:true}).waitFor();};
  await open('Персонализация');await page.locator('[name=preferred_name]').fill('Новое имя');await page.getByRole('button',{name:'Сохранить',exact:true}).click();await page.waitForTimeout(100);assert.equal(calls.at(-1).body.preferred_name,'Новое имя');
  await open('Studio');await page.locator('[name=image_provider]').selectOption('velia_image_2');await page.locator('[name=prompt]').fill('Прозрачный персонаж');await page.locator('[name=transparent_background]').check();await page.getByRole('button',{name:'Создать',exact:true}).click();await page.locator('.feature-card img').waitFor();await page.getByRole('button',{name:'Открыть изображение',exact:true}).click();await page.locator('.file-dialog img').waitFor();assert.match(await page.locator('.file-dialog img').getAttribute('src'),/features\/media\/images/);await page.locator('.file-dialog').getByRole('button',{name:'Закрыть',exact:true}).click();assert.equal(calls.findLast(c=>c.path.endsWith('/generate')).body.image_provider,'velia_image_2');await page.screenshot({path:output+'/studio-desktop.png'});
  await page.getByRole('button',{name:'Видео',exact:true}).click();await page.locator('[name=duration_seconds]').selectOption('15');await page.locator('[name=prompt]').fill('Видео');await page.getByRole('button',{name:'Создать',exact:true}).click();await page.waitForTimeout(100);assert.equal(calls.findLast(c=>c.path.endsWith('/generate')).body.duration_seconds,15);
  for(const name of ['Проекты','Исследования','Медицинский центр','Мои агенты','Автопилот','Плагины','Баланс и использование','Голос']){await open(name);await page.waitForTimeout(100);assert.equal(await page.getByText('Загрузка…',{exact:true}).count(),0);}
  await open('Автопилот');await page.locator('[name=instruction]').fill('Еженедельный обзор');await page.locator('[name=kind]').selectOption('weekly');await page.locator('[name=weekday]').selectOption('4');await page.locator('[name=time]').fill('10:30');await page.getByRole('button',{name:'Создать расписание',exact:true}).click();await page.getByRole('button',{name:'Включить',exact:true}).waitFor();
  const scheduled=calls.findLast(c=>c.path==='agent/schedules'&&c.method==='POST').body;assert.deepEqual(scheduled.schedule,{kind:'weekly',time:'10:30',weekdays:[4]});assert.equal(scheduled.actions[0].tool_name,'velia.tasks.list');assert.ok(scheduled.timezone);
  await page.getByRole('button',{name:'Включить',exact:true}).click();await page.getByRole('button',{name:'Приостановить',exact:true}).click();await page.getByText('Проверить проект',{exact:true}).waitFor();await page.screenshot({path:output+'/autopilot-desktop.png'});
  jobStatus='awaiting_approval';await open('Автопилот');await page.getByRole('button',{name:'Подтвердить действие',exact:true}).waitFor();assert.ok(!calls.some(c=>c.path==='agent/jobs/job1/run'));await page.getByRole('button',{name:'Подтвердить действие',exact:true}).click();await page.getByRole('button',{name:'Выполнить подтверждённое',exact:true}).click();await page.getByText('Завершено',{exact:true}).waitFor();
  schedulerEnabled=false;await open('Автопилот');await page.getByText('Фоновые задачи сейчас отключены на сервере.',{exact:false}).waitFor();assert.equal(await page.getByRole('button',{name:'Создать расписание',exact:true}).count(),0);schedulerEnabled=true;
  await open('Автопилот');await page.getByRole('button',{name:'Разработка',exact:true}).click();await page.getByRole('button',{name:'Открыть',exact:true}).click();await page.getByRole('button',{name:'Активировать',exact:true}).click();await page.waitForTimeout(100);assert.ok(calls.some(c=>c.path==='developer/autopilot/missions/mission1/activate'));
  await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'К диалогу',exact:true}).click();await page.locator('#menu').click();await open('Персонализация');await page.waitForTimeout(500);await page.screenshot({path:output+'/profile-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  for(const width of [360,390,768,1440]) {
    await page.setViewportSize({width,height:960});
    for(const name of ['Медицинский центр','Проекты','Studio','Автопилот','Плагины','Баланс и использование','Голос']) {
      if(width<=900)await page.locator('#menu').click();
      await open(name);await page.waitForTimeout(100);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${name}: overflow at ${width}`);
      assert.equal(await page.locator('#feature-nav button[aria-current=page]').count(),1);
      if(name==='Автопилот'&&width===390)await page.screenshot({path:output+'/autopilot-mobile.png'});
      if(name==='Медицинский центр') {
        const label=page.locator('.feature-check').first();const checkbox=label.locator('input');await label.click();assert.equal(await checkbox.isChecked(),true);
        await page.screenshot({path:output+`/medical-${width}.png`});
      }
    }
  }
  await page.setViewportSize({width:390,height:844});await page.locator('#theme').click();await page.locator('#menu').click();await open('Медицинский центр');await page.waitForTimeout(250);await page.screenshot({path:output+'/medical-light-mobile.png'});
  await page.locator('#menu').click();await page.waitForTimeout(250);await page.screenshot({path:output+'/navigation-mobile.png'});
  await page.locator('#scrim').click({position:{x:380,y:40}});
  await page.getByRole('button',{name:'К диалогу',exact:true}).click();
  for(const height of [640,710,844]) {
    await page.setViewportSize({width:393,height});
    await page.locator('#agent-toggle').click();
    assert.equal(await page.locator('#model-button').isEnabled(),true);
    await page.locator('#model-button').click();
    await page.locator('#model-menu').waitFor({state:'visible'});
    const menu=await page.locator('#model-menu').boundingBox();assert.ok(menu.x>=0&&menu.x+menu.width<=393&&menu.y>=0);
    assert.match(await page.locator('#flash-description').innerText(),/Agent Core/);
    assert.equal(await page.locator('[data-model="velia-quantum"]').isDisabled(),true);
    await page.screenshot({path:output+`/model-agent-${height}.png`});
    await page.locator('#model-button').click();await page.locator('#agent-toggle').click();
    await page.locator('#menu').click();
    const navBox=await page.locator('#feature-nav').boundingBox(),historyBox=await page.locator('.history-heading').boundingBox();
    assert.ok(navBox.y+navBox.height<=historyBox.y+1,'Navigation overlaps history');
    await page.locator('#feature-nav').getByRole('button',{name:'Голос',exact:true}).scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);await page.screenshot({path:output+`/navigation-short-${height}.png`});
    await page.locator('#scrim').click({position:{x:380,y:40}});
  }
  await page.locator('#menu').click();await page.locator('#history .history-open').first().click();await page.locator('#chat-tools').click();await page.locator('#chat-schedule').click();await page.locator('[name=instruction]').waitFor();assert.equal(await page.locator('[name=instruction]').inputValue(),'Моя прежняя идея');assert.equal(await page.locator('[name=template]').inputValue(),'velia.tasks.create_draft');
  await page.getByRole('button',{name:'К диалогу',exact:true}).click();
  await page.locator('#attachment-input').setInputFiles({name:'notes.csv',mimeType:'application/octet-stream',buffer:Buffer.from('<script>window.BAD=true</script>\nname,value\nVELIA,1')});
  await page.locator('.file-chip').getByRole('button',{name:/notes.csv/}).first().click();await page.locator('.file-dialog pre').waitFor();assert.match(await page.locator('.file-dialog pre').innerText(),/<script>/);assert.equal(await page.evaluate(()=>window.BAD),undefined);await page.locator('.file-dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.getByRole('button',{name:'Извлечь текст',exact:true}).click();assert.match(await page.locator('#prompt').inputValue(),/Извлеки текст/);
  const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPmQAAAAASUVORK5CYII=','base64');
  await page.locator('#attachment-input').setInputFiles({name:'photo.png',mimeType:'image/png',buffer:png});await page.locator('.file-chip').getByRole('button',{name:/photo.png/}).first().click();await page.locator('.file-dialog img').waitFor();await page.getByRole('button',{name:'Увеличить',exact:true}).click();assert.equal(await page.locator('.file-dialog img.zoomed').count(),1);await page.screenshot({path:output+'/photo-viewer-mobile.png'});await page.locator('.file-dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.locator('#attachment-input').setInputFiles({name:'report.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\n%%EOF')});await page.locator('.file-chip').getByRole('button',{name:/report.pdf/}).first().click();assert.match(await page.getByRole('link',{name:'Открыть / скачать PDF',exact:true}).getAttribute('href'),/^blob:/);await page.locator('.file-dialog').getByRole('button',{name:'Закрыть',exact:true}).click();
  await page.locator('#attachment-input').setInputFiles({name:'word.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('fixture')});assert.equal(await page.locator('.file-chip').count(),4);
  await page.locator('#attachment-input').setInputFiles({name:'fifth.txt',mimeType:'text/plain',buffer:Buffer.from('too many')});assert.equal(await page.locator('.file-chip').count(),4);
  await page.locator('#attachment-input').setInputFiles({name:'sheet.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:Buffer.from('unsupported')});assert.equal(await page.locator('.file-chip').count(),4);
  await page.getByRole('button',{name:'Удалить notes.csv',exact:true}).click();assert.equal(await page.locator('.file-chip').count(),3);
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Сохранить .md',exact:true}).first().click();assert.equal((await download).suggestedFilename(),'VELIA-answer.md');
  await page.locator('#chat-tools').click();const exportDownload=page.waitForEvent('download');await page.locator('#chat-export').click();assert.equal((await exportDownload).suggestedFilename(),'VELIA-chat.md');
  assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,sections:10,quantumDisabled:true,image2:true,video15:true,autopilotId:true,mobile:true,featureCalls:calls.length}));
}finally{await browser?.close();fixture.kill();}
