/** Synthetic UI acceptance: no real model, GitHub write or scheduled execution. */
import assert from 'node:assert/strict';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM('<html lang="ru"><body><button id="menu"></button><div id="sidebar"></div><div id="scrim"></div><div id="feature-nav"></div><div id="feature-view"></div><div id="conversation-scroll"></div><div class="composer-area"></div></body></html>',{url:'https://velia.example'});
Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location});
const requests=[],listeners=new Set(),toasts=[];
let opened=null,prepared=0,missions=[{mission_id:'other-work',project_id:'repo2',name:'Other repository work',status:'paused'}];
let jobs=[{id:'job1',conversation_id:'chat1',title:'<img src=x onerror=alert(1)>',status:'running',model:'pro'},
  {id:'job2',conversation_id:'chat2',title:'Готовый отчёт',status:'completed',model:'quantum'}];
const emit=()=>{for(const listener of listeners)listener();};
const state={list:()=>jobs,subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn);},poll:async()=>emit(),
  cancel:async job=>{job.status='cancelled';emit();}};
globalThis.fetch=async(url,options={})=>{
  const path=String(url).replace('/web-api/v1/features/','').split('?')[0],method=options.method||'GET',data=options.body?JSON.parse(options.body):null;
  requests.push({path,method,data});
  if(path==='projects')return Response.json({ok:true,projects:[]});
  if(path==='developer/projects')return Response.json({ok:true,projects:[{id:'repo1',repository_full_name:'owner/repo',selected_branch:'main'}]});
  if(path==='developer/autopilot/status')return Response.json({ok:true,worker_ready:true});
  if(path==='developer/autopilot/missions'&&method==='POST'){
    missions.push({mission_id:'work1',project_id:data.project_id,name:data.name,status:'paused'});
    return Response.json({ok:true,mission:missions.at(-1)});
  }
  if(path==='developer/autopilot/missions')return Response.json({ok:true,missions});
  if(path==='developer/autopilot/missions/work1/tasks')return Response.json({ok:true,tasks:[]});
  if(path==='agent/schedules/status')return Response.json({ok:true,enabled:true});
  if(path==='agent/status')return Response.json({ok:true,enabled:true,tools:[{name:'velia.tasks.list',enabled:true}]});
  if(path==='agent/schedules')return Response.json({ok:true,schedules:[]});
  throw new Error('Unexpected request '+method+' '+path);
};
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
const click=text=>{const target=[...document.querySelectorAll('button')].find(button=>button.textContent===text);assert.ok(target,text);target.click();};
try{
  const {setupFeatures}=await import('../web/features.mjs');
  const features=setupFeatures({signedIn:()=>true,isBusy:()=>false,openAuth:()=>assert.fail('unexpected login'),toast:t=>toasts.push(t),
    taskState:()=>state,prepareTask:()=>prepared++,openConversation:async conversation=>{opened=conversation.id;features.close();}});
  assert.ok([...document.querySelectorAll('#feature-nav button')].some(button=>button.textContent.includes('Задачи')));
  assert.ok(!document.querySelector('#feature-nav').textContent.includes('Автопилот'));
  await features.open('autopilot');
  assert.equal(listeners.size,1);assert.ok(!requests.some(request=>request.path.startsWith('developer/')));
  assert.ok(document.querySelector('#feature-view').textContent.includes(jobs[0].title));
  assert.equal(document.querySelector('#feature-view img'),null);
  click('Завершены');await tick();assert.equal(listeners.size,1);
  assert.ok(document.querySelector('#feature-view').textContent.includes('Готовый отчёт'));
  click('Открыть результат');await tick();assert.equal(opened,'chat2');assert.equal(listeners.size,0);
  await features.open('autopilot');click('Поставить задачу в чате');assert.equal(prepared,1);
  click('Отменить');await tick();assert.equal(jobs[0].status,'cancelled');
  click('Завершены');await tick();assert.ok(document.querySelector('#feature-view').textContent.includes('Отменено'));
  click('Запланировано');await tick();assert.equal(listeners.size,0);
  assert.ok(document.querySelector('[name=kind]'));assert.ok(!requests.some(request=>request.method!=='GET'));
  await features.open('projects');
  const repositories=document.querySelector('details');assert.equal(repositories.open,false);
  assert.ok(!requests.some(request=>request.path==='developer/projects'));
  repositories.open=true;repositories.dispatchEvent(new window.Event('toggle'));await tick();
  click('Открыть разработку');await tick();
  assert.ok(!document.querySelector('#feature-view').textContent.includes('Other repository work'));
  assert.ok(!document.querySelector('#feature-view').textContent.includes('Исполнитель разработки готов'));
  const form=document.querySelector('form [name=allowed_paths]').form;
  assert.equal(form.elements.project_id,undefined);
  const advanced=form.querySelector('details');assert.equal(advanced.open,false);
  form.elements.name.value='Улучшить страницу';
  assert.equal(form.checkValidity(),false);
  form.elements.allowed_paths.value='src/\n tests/';
  form.elements.max_files.value='13';assert.equal(form.checkValidity(),false);assert.equal(advanced.open,true);
  form.elements.max_files.value='5';assert.equal(form.checkValidity(),true);
  form.dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();
  const creation=requests.find(request=>request.method==='POST');
  assert.deepEqual(creation.data,{name:'Улучшить страницу',allowed_paths:['src/','tests/'],max_steps:5,max_files:5,project_id:'repo1',blocked_paths:[]});
  assert.equal(missions.at(-1).status,'paused');
  assert.ok(document.querySelector('#feature-view').textContent.includes('Запустить очередь'));
  assert.ok(!requests.some(request=>request.path.endsWith('/activate')));
  features.close();assert.equal(listeners.size,0);
  console.log(JSON.stringify({ok:true,synthetic:true,checks:['shared background states','literal titles','result opens original chat','cancel and finished history','subscriber cleanup','schedules preserved','development inside repository','cross-repository filtering','bounds and path policy preserved','creation stays paused']}));
}catch(error){console.error(error);process.exitCode=1;}finally{dom.window.close();}
