/** Real web entry points with a synthetic account, Figma plugin and model. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8'),{url:'https://velia.example',pretendToBeVisual:true});
const {window}=dom;
Object.assign(globalThis,{window,document:window.document,localStorage:window.localStorage,sessionStorage:window.sessionStorage,location:window.location,matchMedia:()=>({matches:true}),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),BroadcastChannel:undefined});
Object.defineProperty(globalThis,'navigator',{value:window.navigator,configurable:true});
window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
window.HTMLDialogElement.prototype.close=function(){this.open=false;};
const token='figd_synthetic-personal-token',requests=[],model=process.env.VELIA_FIGMA_TEST_MODEL||'velia-pro';
const profile={ok:true,account:'a'.repeat(24),history_cache_key:'b'.repeat(64),language:'ru',credits:10,free_api_tiers:true,background_tasks:true,browser_agent:true,models:['velia-flash','velia-quantum','velia-pro']};
const plans=[{schema:'figma_create_child'},{tool:'figma_create_child',arguments:{parentId:'1:1',nodeType:'FRAME'}},{answer:'Создала фрейм в подключённом макете.'},
 {tool:'figma_create_child',arguments:{parentId:'1:2',nodeType:'TEXT',properties:{text:'Привет'}}},{answer:'Добавила заголовок в тот же макет.'}];
globalThis.fetch=async(url,options={})=>{
 const path=String(url).replace('/web-api/v1/',''),data=options.body?JSON.parse(options.body):null;
 requests.push({path,data});
 if(path==='session')return Response.json(profile);
 if(path==='conversations')return Response.json({ok:true,conversations:[]});
 if(path==='background-tasks')return Response.json({ok:true,jobs:[]});
 if(path==='features/plugins')return Response.json({ok:true,plugins:{}});
 if(path==='figma/status')return Response.json({ok:true,connected:false,status:'disconnected'});
 if(path==='figma/connect'){assert.equal(data.token,token);return Response.json({ok:true,pairing_code:'AB12CD',expires_in:300});}
 if(path==='figma/verify')return Response.json({ok:true,connected:true,connection_id:'fixture-file',file_name:'Fixture design'});
 if(path==='figma/tools')return Response.json({ok:true,tools:[{name:'figma_create_child',description:'Create a child node',inputSchema:{type:'object'}}]});
 if(path==='figma/call')return Response.json({ok:true,isError:false,text:'{"id":"1:2"}'});
 if(path==='figma/plan'){
   assert.equal(data.model,model);assert.equal(data.web_search,false);
   assert.ok(!JSON.stringify(data).includes(token));
   const plan=plans.shift();assert.ok(plan,'Model must not be called again after the final answer');
   const event={choices:[{delta:{content:JSON.stringify(plan)},finish_reason:'stop'}]};
   return new Response('data: '+JSON.stringify(event)+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
 }
 return Response.json({ok:false,error:'fixture_unavailable'},{status:404});
};
const wait=async(check)=>{for(let attempt=0;attempt<100;attempt++){if(check())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Fixture did not finish');};
try {
 await import('../web/app.mjs');await new Promise(resolve=>setTimeout(resolve,40));
 document.querySelector('[data-model="'+model+'"]').click();
 document.querySelector('[data-section="plugins"]').click();
 await wait(()=>document.querySelector('[name="figma-token"]'));
 const card=document.querySelector('[data-figma="connection"]');
 const field=card.querySelector('input');assert.equal(field.type,'password');field.value=token;
 card.querySelector('form').dispatchEvent(new window.Event('submit',{cancelable:true}));
 assert.equal(field.value,'');
 await wait(()=>card.textContent.includes('AB12CD'));
 [...card.querySelectorAll('button')].find(button=>button.textContent==='Проверить подключение').click();
 await wait(()=>card.textContent.includes('Fixture design'));
 [...card.querySelectorAll('button')].find(button=>button.textContent==='Открыть чат с Figma').click();
 document.querySelector('#agent-toggle').click();
 for(const [prompt,expected] of [['Создай фрейм в Figma','Создала фрейм'],['Добавь заголовок Привет','Добавила заголовок']]){
   document.querySelector('#prompt').value=prompt;
   document.querySelector('#composer').dispatchEvent(new window.Event('submit',{cancelable:true}));
   await wait(()=>!document.querySelector('#prompt').disabled&&document.querySelector('#messages').textContent.includes(expected));
 }
 assert.equal(plans.length,0);
 assert.equal(document.querySelector('#agent-toggle').getAttribute('aria-pressed'),'false');
 assert.equal(requests.filter(item=>item.path==='figma/call').length,2);
 assert.equal(requests.filter(item=>item.path.endsWith('/tasks')||item.path.includes('/messages/stream')||item.path.includes('agent/browser')).length,0);
 assert.ok(!document.querySelector('#messages').textContent.includes('"tool"'));
 assert.ok(document.querySelector('#model-label').textContent.includes(model.replace('velia-','').toUpperCase()));
 console.log(JSON.stringify({ok:true,synthetic:true,model,checks:['password field cleared','plugin pairing and verification','main chat creates a frame','follow-up edits the same file','selected model preserved','credential absent from model context','internal plans hidden']}));
} catch(error){console.error(error);process.exitCode=1;}
finally{dom.window.close();setTimeout(()=>process.exit(process.exitCode||0),10);}
