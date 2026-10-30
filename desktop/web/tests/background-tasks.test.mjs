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

test('task hub shares polling, keeps dismissed history and clears snapshots on account changes',async()=>{
  surface();let account='owner',failure=false,calls=0,events=0;
  const controller=setupBackgroundTasks({request:async()=>{calls++;if(failure)throw Error('offline');return{jobs:[{...job,status:'completed'}]};},profile:()=>({account,background_tasks:true}),busy:()=>true,currentId:()=>null,open(){},refresh(){},speak(){},toast(){}});
  const unsubscribe=controller.subscribe(()=>events++);
  try{
    await controller.poll();assert.equal(calls,1);assert.equal(controller.list().length,1);
    const snapshot=controller.list();snapshot[0].title='changed';assert.equal(controller.list()[0].title,job.title);
    failure=true;await controller.poll();assert.deepEqual(controller.status(),{loaded:true,failed:true});assert.equal(controller.list().length,1);
    failure=false;await controller.poll();assert.equal(controller.status().failed,false);
    const before=events;unsubscribe();await controller.poll();assert.equal(events,before);
    account='another-owner';assert.deepEqual(controller.list(),[]);
    controller.reset();assert.deepEqual(controller.status(),{loaded:false,failed:false});
  }finally{controller.reset();unsubscribe();}
});

test('background jobs preserve foreground availability and restore a ready result once',async()=>{
  const nodes=surface();let blocked=true,status='running';let restores=0;const notifications=[];
  const controller=setupBackgroundTasks({request:async()=>({jobs:[{...job,status}]}),profile:()=>({account:'owner',background_tasks:true}),busy:()=>blocked,currentId:()=>job.conversation_id,
    open(){},refresh:async()=>{restores++;},speak(){},toast:text=>notifications.push(text)});
  try{
    controller.modelChanged('velia-pro');assert.equal(nodes['background-enabled'].checked,true);assert.equal(controller.wants('velia-pro'),true);
    nodes['background-enabled'].checked=false;nodes['background-enabled'].onchange();
    controller.modelChanged('velia-flash');controller.modelChanged('velia-pro');assert.equal(controller.wants('velia-pro'),false);
    controller.modelChanged('velia-quantum');assert.equal(controller.wants('velia-quantum'),true);
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

test('closing a finished card survives polling and reload without hiding another account or cancelling a task',async()=>{
  const previousStorage=globalThis.localStorage,stored=new Map(),calls=[];
  globalThis.localStorage={getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)};
  let account='owner',status='completed',controller,nodes;
  const mount=()=>{
    nodes=surface();
    controller=setupBackgroundTasks({request:async(path,data)=>{calls.push({path,data});return {jobs:[{...job,status}]};},
      profile:()=>({account,background_tasks:true}),busy:()=>false,currentId:()=>job.conversation_id,
      open(){},refresh(){},speak(){},toast(){}});
  };
  try{
    mount();await controller.poll();
    nodes['background-tasks'].children[0].children[0].querySelector('button').onclick();
    assert.equal(nodes['background-tasks'].hidden,true);
    await controller.poll();assert.equal(nodes['background-tasks'].hidden,true);
    controller.reset();mount();await controller.poll();assert.equal(nodes['background-tasks'].hidden,true);
    account='other-owner';await controller.poll();assert.equal(nodes['background-tasks'].hidden,false);
    account='owner';status='running';await controller.poll();assert.equal(nodes['background-tasks'].hidden,false);
    assert.ok(calls.every(call=>call.path==='background-tasks'&&call.data===undefined));
    assert.ok([...stored.values()].every(value=>!value.includes(job.title)&&!value.includes(job.result)));
  }finally{controller?.reset();globalThis.localStorage=previousStorage;}
});
