/** Full app synthetic DOM acceptance; no live API, microphone or TTS calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8'),{url:'https://velia.example',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage,sessionStorage:window.sessionStorage,location:window.location,matchMedia:()=>({matches:true}),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),BroadcastChannel:undefined});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};window.HTMLDialogElement.prototype.close=function(){this.open=false;};
const useAgent=process.env.VELIA_SMOKE_BACKGROUND_AGENT==='true',taskModel=process.env.VELIA_SMOKE_BACKGROUND_MODEL||'pro';
const profile={ok:true,account:'a'.repeat(24),history_cache_key:'b'.repeat(64),name:'Owner',language:'ru',credits:10,free_api_tiers:true,background_tasks:true,browser_agent:useAgent,background_agent:useAgent,models:['velia-flash','velia-quantum','velia-pro']};
const cid='00000000-0000-4000-8000-000000000001',jid='00000000-0000-4000-8000-000000000002';
const requests=[],conversations=[],messages=[],jobs=[];
globalThis.fetch=async(url,options={})=>{
 const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;requests.push({path,data});
 if(path==='session')return Response.json({...profile});
 if(path==='background-tasks')return Response.json({ok:true,jobs});
 if(path==='agent/browser/takeover/action')return Response.json({ok:true,finished:true});
 if(path==='conversations'&&data){conversations.push({id:cid,title:data.title});return Response.json({ok:true,conversation:conversations[0]});}
 if(path==='conversations')return Response.json({ok:true,conversations});
 if(path.endsWith('/tasks')){
   messages.push({id:'user-1',role:'user',content:data.content,status:'completed',chat_mode:'pro'});
   if(data.resume_job_id){const parent=jobs.find(j=>j.id===data.resume_job_id);parent.user_action_required=null;parent.resumed=true;}
   jobs.unshift({id:jobs.length?crypto.randomUUID():jid,conversation_id:cid,model:data.model.replace('velia-',''),status:'running',title:data.content,result:'',notes:[],agent:data.agent===true,agent_state:data.agent?'planning':null});
   return Response.json({ok:true,job:jobs[0]},{status:202});
 }
 if(path.endsWith('/messages'))return Response.json({ok:true,messages});
 if(path.endsWith('/messages/stream')){
   messages.push({id:'user-2',role:'user',content:data.content,status:'completed',chat_mode:'flash'},{id:'answer-2',role:'assistant',content:'Flash can keep talking.',status:'completed',chat_mode:'flash'});
   return new Response('data: {"choices":[{"delta":{"content":"Flash can keep talking."},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
 }
 if(path==='voice/voices')return Response.json({voices:[]});
 return Response.json({ok:false,error:'fixture_unavailable'},{status:404});
};
try{
 await import('../web/app.mjs');await new Promise(r=>setTimeout(r,30));
 assert.match(document.querySelector('#flash-description').textContent,/Быстрые ответы/);
 assert.match(document.querySelector('#quantum-description').textContent,/Анализ, тексты/);
 assert.match(document.querySelector('#pro-description').textContent,/Обдумывание сложных задач/);
 assert.match(document.querySelector('#pro-availability').textContent,/токенами/);
 document.querySelector('[data-model="velia-'+taskModel+'"]').click();
 if(useAgent)document.querySelector('#agent-toggle').click();
 assert.equal(document.querySelector('#background-option').hidden,false);
 assert.equal(document.querySelector('#background-enabled').checked,true);
 assert.equal(document.querySelector('#background-enabled').disabled,useAgent);
 document.querySelector('#prompt').value='Prepare a careful comparison';
 document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
 await new Promise(r=>setTimeout(r,50));
 assert.equal(jobs.length,1);assert.equal(document.querySelector('#prompt').disabled,false);
 assert.match(document.querySelector('#model-label').textContent,/FLASH/);
 assert.match(document.querySelector('#background-tasks').textContent,useAgent?/Agent.*Обдумываю задачу/:/PRO.*Готовлю результат/);
 assert.equal(requests.find(r=>r.path.endsWith('/tasks')).data.model,'velia-'+taskModel);
 if(useAgent){assert.equal(requests.find(r=>r.path.endsWith('/tasks')).data.agent,true);assert.equal(document.querySelector('#agent-toggle').getAttribute('aria-pressed'),'false');}
 assert.match(document.querySelector('.background-hint').textContent,/пообщаться с Flash.*в исходном диалоге/);
 // The invitation switches the real composer without resubmitting or losing a draft.
 document.querySelector('[data-model="velia-quantum"]').click();
 document.querySelector('#prompt').value='Meanwhile, explain a term';
 [...document.querySelectorAll('.background-actions button')].find(b=>b.textContent==='Поговорить с Flash').click();
 assert.match(document.querySelector('#model-label').textContent,/FLASH/);
 assert.equal(document.querySelector('#prompt').value,'Meanwhile, explain a term');
 assert.equal(document.activeElement,document.querySelector('#prompt'));
 assert.equal(jobs[0].status,'running');
 document.querySelector('#prompt').value='Meanwhile, explain a term';
 document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
 await new Promise(r=>setTimeout(r,80));
 assert.match(document.querySelector('#messages').textContent,/Flash can keep talking/);
 assert.equal(requests.filter(r=>r.path.endsWith('/tasks')).length,1);
 assert.equal(requests.find(r=>r.path.endsWith('/messages/stream')).data.model,'velia-flash');
 if(useAgent){
   const parent=jobs[0];parent.status='completed';parent.user_action_required='otp';parent.agent_state='waiting_user';
   document.dispatchEvent(new window.Event('visibilitychange'));await new Promise(r=>setTimeout(r,80));
   assert.match(document.querySelector('#background-tasks').textContent,/Нужно действие в браузере/);
   [...document.querySelectorAll('.background-actions button')].find(b=>b.textContent==='Продолжить после ручного шага').click();
   await new Promise(r=>setTimeout(r,80));
   const resumed=requests.filter(r=>r.path.endsWith('/tasks')).at(-1).data;
   assert.equal(resumed.agent,true);assert.equal(resumed.resume_job_id,parent.id);assert.equal(resumed.model,'velia-'+taskModel);
 }
 jobs[0].status='completed';jobs[0].result='The finished PRO comparison.';
 messages.push({id:'answer-1',role:'assistant',content:jobs[0].result,status:'completed',chat_mode:'pro'});
 document.querySelector('#prompt').value='An unsent draft must stay here';
 document.dispatchEvent(new window.Event('visibilitychange'));
 await new Promise(r=>setTimeout(r,80));
 assert.match(document.querySelector('#messages').textContent,/The finished PRO comparison/);
 assert.equal(document.querySelector('.background-hint'),null);
 assert.ok(!document.querySelector('#background-tasks').textContent.includes('Поговорить с Flash'));
 assert.equal(document.querySelector('#prompt').value,'An unsent draft must stay here');
 for(const close of document.querySelectorAll('.background-close'))close.click();
 assert.equal(document.querySelector('#background-tasks').hidden,true);
 document.dispatchEvent(new window.Event('visibilitychange'));
 await new Promise(r=>setTimeout(r,80));
 assert.equal(document.querySelector('#background-tasks').hidden,true);
 assert.match(document.querySelector('#messages').textContent,/The finished PRO comparison/);
 assert.equal(document.querySelector('#prompt').value,'An unsent draft must stay here');
 assert.ok(!requests.some(r=>r.path.endsWith('/cancel')));
 console.log(JSON.stringify({ok:true,synthetic:true,agent:useAgent,taskModel,checks:['model benefits and access terms visible','background task accepted','composer stays available','Flash invitation preserves draft and task','Flash answers while task runs','result restored in original chat','invitation removed on completion','unsent draft preserved','closed card stays hidden after polling','closing preserves answer without cancellation',...(useAgent?['selected model and Agent submitted together','manual-step continuation keeps original task and browser']:[])]}));
}catch(error){console.error(error);process.exitCode=1;}finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
