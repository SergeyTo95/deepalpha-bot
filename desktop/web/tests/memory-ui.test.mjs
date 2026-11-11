import test from 'node:test';
import assert from 'node:assert/strict';
import {mountMemory} from '../memory-ui.mjs';
import {memoryDraft} from '../memory.mjs';

class Element{
  constructor(tag){this.tag=tag;this.children=[];this.dataset={};this.attributes={};this.textContent='';this.value='';this.hidden=false;}
  append(...values){this.children.push(...values);}
  replaceChildren(...values){this.children=[...values];}
  setAttribute(key,value){this.attributes[key]=value;}
  focus(){}
  set innerHTML(_){throw Error('Memory UI must render text, never raw HTML');}
}
const all=root=>[root,...root.children.flatMap(all)];
const find=(root,text)=>all(root).find(e=>e.tag==='button'&&e.textContent===text);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function withUI(run){
  const doc=globalThis.document,confirm=globalThis.confirm;globalThis.document={createElement:tag=>new Element(tag)};globalThis.confirm=()=>true;
  try{await run();}finally{globalThis.document=doc;globalThis.confirm=confirm;}
}
const fact=(id,text)=>({...memoryDraft(text,{kind:'chat',id:'chat-one',messageId:'one',title:'Исходный чат',quote:text,role:'user',at:1794400000000}),id,updated:10});
function fixture(values=[]){
  const entries=structuredClone(values),toasts=[],saves=[];
  const notebook={account:'a',epoch:1,load:async()=>structuredClone(entries),
    save:async(value,options)=>{saves.push({value,options});const i=entries.findIndex(e=>e.id===value.id);if(i>=0)entries[i]=value;else entries.push({...value,id:value.id||'new'});},
    remove:async id=>{entries.splice(entries.findIndex(e=>e.id===id),1);},mutate:async change=>change(entries)};
  return {entries,toasts,saves,notebook};
}

test('memory editor saves corrected content and entity links while retaining the original source',async()=>withUI(async()=>{
  const value=fact('m1','По проекту Velia решили закончить веб-версию'),f=fixture([value]),root=new Element('div');
  await mountMemory(root,{...f,toast:t=>f.toasts.push(t),signal:new AbortController().signal,draft:{id:'m1'}});
  const form=all(root).find(e=>e.tag==='form'),labels=form.children.filter(e=>e.tag==='label');
  labels.find(e=>e.textContent==='Что помнить').children[0].value='Velia: решение уточнили';
  labels.find(e=>e.textContent==='Человек · через запятую').children[0].value='Иван';
  form.onsubmit({preventDefault(){}});await flush();await flush();
  assert.equal(f.saves[0].value.content,'Velia: решение уточнили');assert.equal(f.saves[0].value.memory.source.quote,value.content);
  assert.ok(f.saves[0].value.memory.entities.some(e=>e.kind==='person'&&e.name==='Иван'));assert.equal(f.saves[0].options.expectedUpdated,10);
  assert.ok(f.toasts.some(t=>t.includes('Сохранила')));
}));

test('memory controls disable retrieval, open the source, and clear memories without deleting notes',async()=>withUI(async()=>{
  const f=fixture([fact('m1','По проекту Velia решили выпустить сайт'),{id:'note',kind:'note',title:'Заметка',content:'Останется',tags:[]}]),root=new Element('div');let opened;
  await mountMemory(root,{notebook:f.notebook,toast:t=>f.toasts.push(t),signal:new AbortController().signal,openSource:s=>{opened=s;}});
  await find(root,'Открыть источник').onclick();assert.equal(opened.id,'chat-one');
  const enabled=all(root).find(e=>e.tag==='input'&&e.type==='checkbox');enabled.checked=false;enabled.onchange();await flush();await flush();
  assert.equal(f.entries.find(e=>e.kind==='memory-settings').enabled,false);
  await find(root,'Удалить всю память').onclick();assert.equal(f.entries.some(e=>e.kind==='memory'),false);assert.ok(f.entries.some(e=>e.id==='note'));
}));

test('closed memory panels and changed accounts cannot save a stale draft',async()=>withUI(async()=>{
  for(const change of ['abort','account']){
    const f=fixture(),root=new Element('div'),controller=new AbortController();
    await mountMemory(root,{notebook:f.notebook,toast:()=>{},signal:controller.signal,draft:{entry:fact('draft','Личный факт')}});
    const form=all(root).find(e=>e.tag==='form');if(change==='abort')controller.abort();else{f.notebook.account='b';f.notebook.epoch++;}
    form.onsubmit({preventDefault(){}});await flush();assert.equal(f.saves.length,0);
  }
}));

test('source quotations and project names render as plain text',async()=>withUI(async()=>{
  const value=fact('m1','<img src=x onerror=alert(1)>'),f=fixture([value]),root=new Element('div');
  await mountMemory(root,{notebook:f.notebook,toast:()=>{},signal:new AbortController().signal});
  assert.ok(all(root).some(e=>e.textContent===value.content));assert.equal(all(root).some(e=>e.tag==='img'),false);
}));
