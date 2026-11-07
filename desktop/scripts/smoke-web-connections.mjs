/** Whole-app account connections with synthetic services and model responses. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const provider=process.env.VELIA_CLOUD_PROVIDER||'vercel',model=process.env.VELIA_TEST_CHAT_MODEL||'velia-pro';
const dom=new JSDOM(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8'),{url:'https://velia.example/?connection='+provider+'&result=connected',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage,sessionStorage:window.sessionStorage,location:window.location,matchMedia:()=>({matches:true}),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),BroadcastChannel:undefined});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;};
const profile={ok:true,account:'a'.repeat(24),history_cache_key:'b'.repeat(64),language:'ru',credits:10,free_api_tiers:true,background_tasks:true,browser_agent:true,models:['velia-flash','velia-quantum','velia-pro']};
let selected=false;
const calls=[],plans=[{tool:'list_projects',arguments:{}},{answer:'Найден проект example.'},
  {tool:'list_deployments',arguments:{project:'project-1'}},{answer:'Последний деплой завершён успешно.'}];
globalThis.fetch=async(url,options={})=>{
  const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;
  calls.push({path,data});
  if(path==='session')return Response.json(profile);
  if(path==='conversations')return Response.json({ok:true,conversations:[]});
  if(path==='background-tasks')return Response.json({ok:true,jobs:[]});
  if(path==='features/projects')return Response.json({ok:true,projects:[]});
  if(path==='features/developer/projects'&&!data)return Response.json({ok:true,projects:selected?[{id:'p',repository_full_name:'owner/example',selected_branch:'main'}]:[]});
  if(path==='figma/status')return Response.json({ok:true,connected:false,status:'disconnected',oauth_available:true});
  if(path==='features/plugins')return Response.json({ok:true,plugins:{}});
  if(path==='features/developer/github/disconnect')return Response.json({ok:true,connected:false});
  if(/^connections\/(?:railway|vercel|google|notion|shopify)\/disconnect$/.test(path))return Response.json({ok:true,connected:false});
  if(path==='features/developer/status')return Response.json({ok:true,enabled:true,github_configured:true,web_connect_configured:true,installation_count:1});
  if(path==='features/developer/installations')return Response.json({ok:true,installations:[{installation_id:123}]});
  if(path==='features/developer/repositories?installation_id=123')return Response.json({ok:true,repositories:[{id:456,full_name:'owner/example',default_branch:'main'}]});
  if(path==='features/developer/projects'&&data){assert.deepEqual(data,{installation_id:123,repository_id:456,branch:'main'});selected=true;return Response.json({ok:true,project:{id:'p'}});}
  if(/^connections\/(?:railway|vercel|google|notion|shopify)\/(?:status|verify)$/.test(path))return Response.json({ok:true,connected:true,connection_id:'account-1',tool_count:2});
  if(path==='connections/'+provider+'/tools')return Response.json({ok:true,tools:['list_projects','list_deployments'].map(name=>({name,description:name,inputSchema:{type:'object'}}))});
  if(path==='connections/'+provider+'/call')return Response.json({ok:true,isError:false,text:JSON.stringify(data.name==='list_projects'?{projects:[{id:'project-1',name:'example'}]}:{deployments:[{status:'SUCCESS'}]})});
  if(path==='connections/'+provider+'/plan'){
    assert.equal(data.model,model);assert.equal(data.web_search,false);
    const plan=plans.shift();assert.ok(plan);
    const event={choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]};
    return new Response('data: '+JSON.stringify(event)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
  }
  return Response.json({ok:false,error:'fixture_unavailable'},{status:404});
};
const wait=async(check)=>{for(let attempt=0;attempt<100;attempt++){if(check())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Fixture timeout');};
try{
  await import('../web/app.mjs');
  await wait(()=>document.querySelector('[data-connection="'+provider+'"]'));
  assert.equal(location.search,'');
  document.querySelector('[data-model="'+model+'"]').click();
  await wait(()=>[...document.querySelectorAll('.connection-toggle')].every(button=>!button.disabled));
  const rows=[...document.querySelectorAll('.connection-row')];
  assert.equal(rows.length,7);
  for(const row of rows){assert.equal(row.querySelectorAll('button').length,1);assert.ok(row.querySelector('.connection-icon svg path'));assert.equal(row.firstElementChild.className,'connection-icon');}
  assert.ok(!document.querySelector('.connections-list').textContent.includes('Проверить'));
  assert.equal(calls.filter(call=>call.path.endsWith('/verify')).length,0);
  document.querySelector('[data-section="projects"]').click();
  await wait(()=>[...document.querySelectorAll('summary')].some(item=>item.textContent==='Репозитории и разработка'));
  const repositories=[...document.querySelectorAll('summary')].find(item=>item.textContent==='Репозитории и разработка').parentElement;
  repositories.open=true;
  const picker=[...repositories.querySelectorAll('summary')].find(item=>item.textContent==='Добавить репозиторий из GitHub').parentElement;
  picker.open=true;
  await wait(()=>picker.querySelector('button'));
  picker.querySelector('button').click();
  await wait(()=>calls.some(call=>call.path==='features/developer/projects'&&call.data));
  [...document.querySelectorAll('.feature-header button')].find(button=>button.textContent==='К диалогу').click();
  document.querySelector('#agent-toggle').click();
  for(const [prompt,answer] of [['Покажи проекты в '+provider,'Найден проект example'],['Покажи последние деплои','завершён успешно']]){
    document.querySelector('#prompt').value=prompt;
    document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
    await wait(()=>!document.querySelector('#prompt').disabled&&document.querySelector('#messages').textContent.includes(answer));
  }
  assert.equal(calls.filter(call=>call.path==='connections/'+provider+'/verify').length,2);
  assert.equal(plans.length,0);assert.equal(calls.filter(call=>call.path==='connections/'+provider+'/call').length,2);
  assert.ok(!calls.some(call=>call.path.includes('agent/browser')||call.path.endsWith('/tasks')));
  assert.ok(document.querySelector('#model-label').textContent.includes(model.replace('velia-','').toUpperCase()));
  document.querySelector('[data-section="plugins"]').click();
  await wait(()=>[...document.querySelectorAll('.connection-toggle')].every(button=>!button.disabled));
  for(const id of ['github','railway','vercel','google','notion','shopify']){
    const row=document.querySelector('[data-connection="'+id+'"]'),button=row.querySelector('button');
    assert.equal(button.textContent,'Отключить');
    button.click();button.click();
    await wait(()=>button.textContent==='Подключить'&&!button.disabled);
    assert.equal(row.dataset.connected,'false');
  }
  assert.equal(calls.filter(call=>call.path==='features/developer/github/disconnect').length,1);
  assert.equal(calls.filter(call=>call.path==='connections/'+provider+'/disconnect').length,1);
  console.log(JSON.stringify({ok:true,synthetic:true,provider,model,checks:['OAuth return opens connections','GitHub repository selected','one button per service; no manual checks','repository selection in Projects','automatic verification before each task','project and deployment tools through main chat','follow-up uses same provider','selected model preserved','disconnect changes the same button; double click ignored']}));
}catch(error){console.error(error);process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
