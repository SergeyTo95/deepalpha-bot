/** Synthetic form acceptance; no real accounts, model calls or website actions. */
import assert from 'node:assert/strict';
const {JSDOM}=await import(process.env.VELIA_JSDOM_MODULE||'jsdom');
const dom=new JSDOM('<html lang="ru"><body><button id="menu"></button><div id="sidebar"></div><div id="scrim"></div><div id="feature-nav"></div><div id="feature-view"></div><div id="conversation-scroll"></div><div class="composer-area"></div></body></html>',{url:'https://velia.example'});
Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location});
let profiles=[],opened=null;const requests=[],toasts=[];
const caps=[{id:'velyon.core.reasoning',name:'Рассуждения'},{id:'velyon.focus.analysis',name:'Анализ'}];
globalThis.fetch=async(url,options={})=>{
 const path=String(url).replace('/web-api/v1/features/','').split('?')[0],method=options.method||'GET',data=options.body?JSON.parse(options.body):null;
 requests.push({path,method,data});
 if(path==='agents/capabilities')return Response.json({ok:true,capabilities:caps});
 if(path==='agents'&&method==='POST'){profiles.push({id:'agent-1',...data,capabilities:caps.filter(c=>data.capability_ids.includes(c.id))});return Response.json({ok:true,agent:profiles[0]});}
 if(path==='agents')return Response.json({ok:true,agents:profiles});
 if(path==='agents/agent-1'&&method==='PATCH'){profiles[0]={...profiles[0],...data};return Response.json({ok:true,agent:profiles[0]});}
 if(path==='agents/agent-1')return Response.json({ok:true,agent:profiles[0]});
 if(path==='agents/agent-1/conversations')return Response.json({ok:true,conversation:{id:'conversation-1',title:profiles[0].name}});
 throw new Error('Unexpected request: '+method+' '+path);
};
const tick=()=>new Promise(r=>setTimeout(r,20));
const click=text=>{const button=[...document.querySelectorAll('button')].find(b=>b.textContent===text);assert.ok(button,text);button.click();};
try{
 const {setupFeatures}=await import('../web/features.mjs');
 const features=setupFeatures({signedIn:()=>true,openAuth:()=>assert.fail('unexpected login'),toast:t=>toasts.push(t),isBusy:()=>false,openConversation:async c=>{opened=c;}});
 await features.open('agents');
 let form=document.querySelector('form'),toggle=form.elements.spec_mode_enabled;
 assert.equal(toggle.checked,false);assert.equal(toggle.getAttribute('role'),'switch');
 form.elements.name.value='My planner';form.elements.instructions.value='Help with my project';
 form.elements.capability_ids.options[1].selected=true;toggle.checked=true;
 form.dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();
 assert.equal(profiles.length,1);assert.equal(profiles[0].spec_mode_enabled,true);
 assert.equal(requests.find(r=>r.method==='POST').data.spec_mode_enabled,true);
 click('Открыть');click('Настроить');await tick();
 form=[...document.querySelectorAll('form')].at(-1);toggle=form.elements.spec_mode_enabled;
 assert.equal(toggle.checked,true);assert.equal(form.elements.capability_ids.selectedOptions[0].value,'velyon.focus.analysis');
 toggle.checked=false;form.dispatchEvent(new window.Event('submit',{cancelable:true}));await tick();
 assert.equal(profiles[0].spec_mode_enabled,false);
 assert.equal(requests.find(r=>r.method==='PATCH').data.spec_mode_enabled,false);
 assert.equal(requests.find(r=>r.method==='PATCH').data.can_create_chats,undefined);
 await features.open('agents');click('Открыть');click('Настроить');await tick();
 assert.equal([...document.querySelectorAll('form')].at(-1).elements.spec_mode_enabled.checked,false);
 click('Отмена');await tick();click('Открыть');click('Новый диалог');await tick();
 assert.equal(opened.id,'conversation-1');assert.equal(document.querySelector('#feature-view').hidden,true);
 assert.ok(!toasts.some(t=>t.includes('ошибка')));
 console.log(JSON.stringify({ok:true,synthetic:true,checks:['off by default','boolean create','edit persists off','selected capabilities preserved','reopen restores preference','same agent conversation']}));
}catch(error){console.error(error);process.exitCode=1;}finally{dom.window.close();}
