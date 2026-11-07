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
const calls=[],plans=[{tool:'list_projects',arguments:{}},{answer:'Найден проект example.'},
  {tool:'list_deployments',arguments:{project:'project-1'}},{answer:'Последний деплой завершён успешно.'}];
globalThis.fetch=async(url,options={})=>{
  const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;
  calls.push({path,data});
  if(path==='session')return Response.json(profile);
  if(path==='conversations')return Response.json({ok:true,conversations:[]});
  if(path==='background-tasks')return Response.json({ok:true,jobs:[]});
  if(path==='features/plugins')return Response.json({ok:true,plugins:{}});
  if(path==='features/developer/status')return Response.json({ok:true,enabled:true,github_configured:true,web_connect_configured:true,installation_count:1});
  if(path==='features/developer/installations')return Response.json({ok:true,installations:[{installation_id:123}]});
  if(path==='features/developer/repositories?installation_id=123')return Response.json({ok:true,repositories:[{id:456,full_name:'owner/example',default_branch:'main'}]});
  if(path==='features/developer/projects'&&data){assert.deepEqual(data,{installation_id:123,repository_id:456,branch:'main'});return Response.json({ok:true,project:{id:'p'}});}
  if(/^connections\/(?:railway|vercel)\/(?:status|verify)$/.test(path))return Response.json({ok:true,connected:true,connection_id:'account-1',tool_count:2});
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
  const choose=[...document.querySelectorAll('button')].find(button=>button.textContent==='Выбрать репозиторий');choose.click();
  await wait(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='Использовать в Велии'));
  [...document.querySelectorAll('button')].find(button=>button.textContent==='Использовать в Велии').click();
  await wait(()=>calls.some(call=>call.path==='features/developer/projects'&&call.data));
  const card=document.querySelector('[data-connection="'+provider+'"]');
  assert.equal(card.querySelector('input'),null);
  [...card.querySelectorAll('button')].find(button=>button.textContent.startsWith('Открыть чат с')).click();
  document.querySelector('#agent-toggle').click();
  for(const [prompt,answer] of [['Покажи проекты в '+provider,'Найден проект example'],['Покажи последние деплои','завершён успешно']]){
    document.querySelector('#prompt').value=prompt;
    document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
    await wait(()=>!document.querySelector('#prompt').disabled&&document.querySelector('#messages').textContent.includes(answer));
  }
  assert.equal(plans.length,0);assert.equal(calls.filter(call=>call.path==='connections/'+provider+'/call').length,2);
  assert.ok(!calls.some(call=>call.path.includes('agent/browser')||call.path.endsWith('/tasks')));
  assert.ok(document.querySelector('#model-label').textContent.includes(model.replace('velia-','').toUpperCase()));
  console.log(JSON.stringify({ok:true,synthetic:true,provider,model,checks:['OAuth return opens connections','GitHub repository selected','no manual token field','project and deployment tools through main chat','follow-up uses same provider','selected model preserved']}));
}catch(error){console.error(error);process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
