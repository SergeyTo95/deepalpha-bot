import test from 'node:test';
import assert from 'node:assert/strict';
import {miniAppNode} from '../mini-app-ui.mjs';
import {startupApp,budgetApp} from '../mini-apps.mjs';
import {initialAppState,calculateApp} from '../mini-app-core.mjs';
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';this.value='';}
  append(...children){for(const c of children)c.parent=this;this.children.push(...children);}
  replaceChildren(...children){this.children=[];this.append(...children);}
  replaceWith(next){const i=this.parent.children.indexOf(this);this.parent.children[i]=next;next.parent=this.parent;}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  set innerHTML(_){throw Error('Apps must not render raw HTML');}
}
const nodes=e=>[e,...e.children.flatMap(nodes)];
const find=(e,text)=>nodes(e).find(n=>n.tag==='button'&&n.textContent===text);
async function withDOM(run){const old=globalThis.document;globalThis.document={createElement:t=>new Element(t),createElementNS:(_ns,t)=>new Element(t)};try{await run();}finally{globalThis.document=old;}}
test('editing a calculator recalculates outputs and graph locally, preserves blank-field errors, and saves current state',async()=>withDOM(async()=>{
  const changes=[],saved=[],root=miniAppNode(initialAppState(startupApp()),{signal:new AbortController().signal,onChange:s=>changes.push(s),onSave:async s=>saved.push(s)});
  const input=nodes(root).find(e=>e.attributes['aria-label']==='Годовая выручка');input.value='2000000';input.oninput();
  assert.equal(calculateApp(changes.at(-1)).outputs.enterprise,10000000);assert.ok(nodes(root).some(e=>e.tag==='strong'&&e.textContent.replace(/\s/gu,'').includes('10000000')));
  assert.ok(nodes(root).some(e=>e.tag==='svg'&&e.attributes.role==='img'));assert.ok(nodes(root).some(e=>e.tag==='table'));
  input.value='';input.oninput();assert.equal(find(root,'Сохранить в заметки').disabled,true);assert.equal(input.attributes['aria-invalid'],'true');
  const multiple=nodes(root).find(e=>e.attributes['aria-label']==='Мультипликатор выручки');multiple.value='7';multiple.oninput();assert.equal(find(root,'Сохранить в заметки').disabled,true,'another valid field must not clear a blank field error');
  input.value='1000000';input.oninput();assert.equal(find(root,'Сохранить в заметки').disabled,false);
  await find(root,'Сохранить в заметки').onclick();assert.equal(saved[0].values.multiple,7);
}));
test('editable table rows, totals and negative chart values update and remain finite',async()=>withDOM(async()=>{
  const changes=[],root=miniAppNode(initialAppState(budgetApp()),{signal:new AbortController().signal,onChange:s=>changes.push(s)});
  const value=nodes(root).find(e=>e.attributes['aria-label']==='Проект B · Доход');value.value='10000';value.oninput();assert.equal(calculateApp(changes.at(-1)).totals.profit,11000);
  find(root,'Добавить строку').onclick();assert.equal(changes.at(-1).rows.length,4);
  const remove=nodes(root).find(e=>e.attributes['aria-label']==='Удалить строку 4');remove.onclick();assert.equal(changes.at(-1).rows.length,3);
  for(const shape of nodes(root).filter(e=>['rect','circle','polyline'].includes(e.tag)))assert.doesNotMatch(JSON.stringify(shape.attributes),/NaN|Infinity/);
}));
test('reset restores the initial parameters and chart choice without re-requesting a model',async()=>withDOM(async()=>{
  const changed=[],parent=new Element('div'),root=miniAppNode(initialAppState(startupApp()),{signal:new AbortController().signal,onChange:s=>changed.push(s)});parent.append(root);
  const value=nodes(root).find(e=>e.attributes['aria-label']==='Годовая выручка');value.value='2000000';value.oninput();find(root,'Сбросить параметры').onclick();
  assert.equal(changed.at(-1).values.revenue,1000000);assert.notEqual(parent.children[0],root);
}));
test('labels are plain text and aborted or changed-owner cards cannot persist edits',async()=>withDOM(async()=>{
  const spec=startupApp();spec.title='<img src=x onerror=alert(1)>';spec.inputs[0].label='<script>alert(1)</script>';
  const controller=new AbortController(),changed=[],root=miniAppNode(initialAppState(spec),{signal:controller.signal,onChange:s=>changed.push(s)});
  assert.ok(nodes(root).some(e=>e.tag==='h3'&&e.textContent===spec.title));assert.equal(nodes(root).some(e=>e.tag==='script'||e.tag==='img'),false);
  controller.abort();const input=nodes(root).find(e=>e.tag==='input');input.value='12';input.oninput();assert.equal(changed.length,0);
  const locked=miniAppNode(initialAppState(startupApp()),{signal:new AbortController().signal,guard:()=>{throw new DOMException('Changed','AbortError');},onChange:s=>changed.push(s)});const stale=nodes(locked).find(e=>e.tag==='input');stale.value='123';stale.oninput();assert.equal(changed.length,0);
}));
test('read-only cards disable editing and saving during an active turn',async()=>withDOM(async()=>{
  const root=miniAppNode(initialAppState(startupApp()),{signal:new AbortController().signal,readOnly:true});assert.equal(nodes(root).find(e=>e.tag==='fieldset').disabled,true);assert.equal(find(root,'Сохранить в заметки').disabled,true);assert.equal(find(root,'Сбросить параметры').disabled,true);
}));
