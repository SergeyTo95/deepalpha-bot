import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAutoloop,setupAutoloop} from '../autoloop.mjs';

test('only explicit recurring instructions create a rule and preserve the task text',()=>{
  const rule=parseAutoloop('Повторяй каждые 2 часа: Проверь сайт и сообщи об ошибках');
  assert.equal(rule.interval_minutes,120);
  assert.equal(rule.instruction,'Проверь сайт и сообщи об ошибках');
  assert.equal(rule.max_runs_per_day,1);
  assert.equal(parseAutoloop('Repeat every 30 minutes: Check the release').interval_minutes,30);
  assert.equal(parseAutoloop('Как проверять сайт каждые 30 минут?'),null);
  assert.equal(parseAutoloop('Напиши пример: Repeat every 30 minutes: Check'),null);
  assert.equal(parseAutoloop('Объясни Agent Autoloop'),null);
  assert.throws(()=>parseAutoloop('Повторяй каждые 1 минуту: Проверь сайт'));
});

function surface(){
  class Element{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.hidden=false;}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
    get childElementCount(){return this.children.length;}
  }
  const panel=new Element('section');
  globalThis.document={getElementById:()=>panel,createElement:tag=>new Element(tag),addEventListener(){}};
  return panel;
}

test('a response for a signed-out account cannot render saved recurring instructions',async()=>{
  const panel=surface();let account='one',resolve;
  const controller=setupAutoloop({request:()=>new Promise(r=>resolve=r),profile:()=>account?{account,background_tasks:true}:null,
    currentId:()=> 'conversation',toast(){}});
  const pending=controller.poll();account=null;controller.reset();
  resolve({rules:[{id:'rule',conversation_id:'conversation',instruction:'PRIVATE',enabled:true}]});
  await pending;assert.equal(panel.childElementCount,0);assert.equal(panel.hidden,true);
});

test('saved rules render text safely and use owner-scoped pause and manual-event controls',async()=>{
  const panel=surface(),calls=[];
  const rule={id:'rule',conversation_id:'conversation',instruction:'<img src=x onerror=alert(1)>',enabled:true,
    interval_minutes:60,max_runs_per_day:1};
  const controller=setupAutoloop({request:async(path,data)=>{calls.push({path,data});return {rules:[rule]};},
    profile:()=>({account:'owner',background_tasks:true}),currentId:()=> 'conversation',toast(){}});
  try{
    await controller.poll();assert.equal(panel.childElementCount,1);
    const card=panel.children[0];assert.equal(card.children[1].textContent,rule.instruction);
    await card.children[3].children[0].onclick();
    assert.equal(calls[1].path,'autoloop/rules/rule/disable');assert.deepEqual(calls[1].data,{});
  }finally{controller.reset();}
});
