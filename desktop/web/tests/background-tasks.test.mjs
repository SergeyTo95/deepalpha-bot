import test from 'node:test';
import assert from 'node:assert/strict';
import {setupBackgroundTasks,taskAcknowledgement} from '../background-tasks.mjs';

function surface(){
  class Element{
    constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.hidden=false;this.value='';}
    append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;}
    get childElementCount(){return this.children.length;}
    contains(node){return this===node||this.children.some(child=>child.contains?.(node));}
    querySelector(tag){return this.children.find(c=>c.tagName===tag.toUpperCase())||this.children.map(c=>c.querySelector?.(tag)).find(Boolean);}
    setAttribute(){}
    focus(){document.activeElement=this;}
    blur(){document.activeElement=null;}
  }
  const nodes=Object.fromEntries(['background-tasks','background-option','background-enabled'].map(id=>[id,new Element('div')]));
  nodes['background-option'].hidden=true;
  globalThis.document={hidden:false,activeElement:null,getElementById:id=>nodes[id],createElement:tag=>new Element(tag),addEventListener(){}};
  return nodes;
}
const job={id:'00000000-0000-4000-8000-000000000001',conversation_id:'conversation',model:'pro',status:'running',title:'<img src=x onerror=alert(1)>',result:'Answer',notes:[]};

test('background jobs preserve foreground availability and restore a ready result once',async()=>{
  const nodes=surface();let blocked=true,status='running';let restores=0;const notifications=[];
  const controller=setupBackgroundTasks({request:async()=>({jobs:[{...job,status}]}),profile:()=>({account:'owner',background_tasks:true}),busy:()=>blocked,currentId:()=>job.conversation_id,
    open(){},refresh:async()=>{restores++;},speak(){},toast:text=>notifications.push(text)});
  try{
    controller.modelChanged('velia-pro');nodes['background-enabled'].checked=true;assert.equal(controller.wants('velia-pro'),true);
    await controller.poll();assert.equal(restores,0);assert.equal(nodes['background-tasks'].hidden,false);
    const card=nodes['background-tasks'].children[0];assert.equal(card.children[1].textContent,job.title);
    status='completed';await controller.poll();assert.equal(restores,0);assert.equal(notifications.length,1);
    blocked=false;await controller.poll();assert.equal(restores,1);await controller.poll();assert.equal(restores,1);assert.equal(notifications.length,1);
    controller.modelChanged('velia-flash');assert.equal(controller.wants('velia-flash'),false);
  }finally{controller.reset();}
});

test('a response from a signed-out owner cannot restore task data or notifications',async()=>{
  const nodes=surface();let account={account:'owner',background_tasks:true},resolve;let notices=0;
  const controller=setupBackgroundTasks({request:()=>new Promise(r=>resolve=r),profile:()=>account,busy:()=>false,currentId:()=>job.conversation_id,open(){},refresh(){throw Error('cross-account restore');},speak(){},toast(){notices++;}});
  const pending=controller.poll();account=null;controller.reset();resolve({jobs:[{...job,status:'completed'}]});await pending;
  assert.equal(nodes['background-tasks'].childElementCount,0);assert.equal(notices,0);
});

test('task submission uses one request and retains its idempotency key',async()=>{
  const nodes=surface(),calls=[];
  const controller=setupBackgroundTasks({request:async(path,data)=>{calls.push({path,data});return {job};},profile:()=>({account:'owner',background_tasks:true}),busy:()=>false,currentId:()=>job.conversation_id,open(){},refresh(){},speak(){},toast(){}});
  try{
    const payload={content:'Task',model:'velia-pro',idempotency_key:'stable-request-key'};
    assert.equal((await controller.submit('conversation',payload)).id,job.id);
    assert.deepEqual(calls,[{path:'conversations/conversation/tasks',data:payload}]);
    assert.equal(nodes['background-tasks'].hidden,false);
    assert.match(taskAcknowledgement('ru-RU'),/готовлю/);
    assert.notEqual(taskAcknowledgement('tr'),taskAcknowledgement('ru'));
    assert.equal(taskAcknowledgement('unknown-locale'),'');
  }finally{controller.reset();}
});
