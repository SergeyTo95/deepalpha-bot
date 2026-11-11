import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook,validateEntry} from '../notebook.mjs';
import {MEMORY_SETTINGS_ID} from '../memory-schema.mjs';
import {memoryCommand,memoryDraft,memorySource,memoryGraph,recallMemory,recallMemoryAnswer} from '../memory.mjs';

const source=(quote='Исходная цитата')=>({kind:'chat',id:'chat-one',messageId:'message-one',title:'Обсуждение Velia',quote,role:'user',at:1794400000000});
const entry=(id,content,entities=[])=>({...memoryDraft(content,source(content)),id,updated:1794400000000,memory:{...memoryDraft(content,source(content)).memory,entities}});
const project={kind:'project',name:'Velia'};
const stream=(value,finish='stop')=>new Response('data: '+JSON.stringify({choices:[{delta:{content:typeof value==='string'?value:JSON.stringify(value)},finish_reason:finish}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
function fixture(){
  let user='a';const records=new Map(),keys={a:'1'.repeat(64),b:'2'.repeat(64)};
  const request=async(path,options={})=>{
    assert.equal(path,'notebook');const old=records.get(user)||{revision:0,record:null};
    if(options.method==='PUT'){
      assert.equal(options.data.account,user.repeat(24));assert.equal(options.data.owner,user);
      if(options.data.revision!==old.revision)throw Object.assign(Error('conflict'),{status:409});
      records.set(user,{revision:old.revision+1,record:options.data.record});return {revision:old.revision+1};
    }
    return {...old,account:user.repeat(24),owner:user,encryption_key:keys[user]};
  };
  return {book:new Notebook(request,webcrypto),other:()=>new Notebook(request,webcrypto),records,setUser:value=>user=value};
}

test('memory commands require explicit saving and do not intercept timed reminders or ordinary actions',()=>{
  for(const input of ['Запомни: проект Velia — сначала веб-версия','Запомни, пожалуйста: предпочитаю русский','Remember that Velia uses a free route service'])assert.equal(memoryCommand(input)?.kind,'save');
  for(const input of ['Что мы решили по проекту Velia?','Напомни, что мы решили по Velia','Напомни, о чём мы договорились по Velia?','Что ты помнишь обо мне?','Найди в памяти Velia'])assert.equal(memoryCommand(input)?.kind,'recall',input);
  assert.equal(memoryCommand('Забудь решение по Velia').kind,'manage');
  for(const input of ['Напомни завтра в 10 позвонить Ивану','Напомни о встрече через час','Напомни завтра, что мы решили по Velia','Напомни мне через час о договорённостях','Как запомнить пароль?','Собери финансовое состояние проектов','Создай задачу по Velia'])assert.equal(memoryCommand(input),null,input);
});

test('saved data preserves exact source, permits corrected content and rejects malformed lineage',()=>{
  const value=entry('m1','По проекту Velia решили сначала веб-версию',[project]);
  assert.equal(validateEntry(value),value);assert.equal(value.memory.source.quote,value.content);
  assert.equal(validateEntry({...value,content:'Уточнённое решение'}).memory.source.quote,value.content);
  for(const bad of [{...value,content:''},{...value,content:'x'.repeat(4001)},
    {...value,memory:{...value.memory,enabled:1}}, {...value,memory:{...value.memory,entities:[project,project]}},
    {...value,memory:{...value.memory,source:{...source(),messageId:''}}},
    {...value,memory:{...value.memory,source:{...source(),at:Number.MAX_SAFE_INTEGER}}}])assert.throws(()=>validateEntry(bad));
  const message={role:'assistant',content:'Точный ответ',sentAt:1794400000000};
  const a=memorySource({id:'chat-two',title:'Встреча'},message),b=memorySource({id:'chat-two',title:'Встреча'},message);assert.equal(a.messageId,b.messageId);assert.equal(a.quote,message.content);assert.equal(a.role,'assistant');
});

test('graph joins only explicit shared entities and draft detects existing names without substring collisions',()=>{
  const first=entry('a','По проекту Velia решили закончить веб-версию',[project,{kind:'person',name:'Иван'}]);
  const second=entry('b','Иван подготовит бюджет',[{kind:'person',name:'Иван'}, {kind:'project',name:' velia '}]);
  const graph=memoryGraph([first,second]);assert.equal(graph.nodes.length,4);assert.equal(graph.edges.length,4);
  const draft=memoryDraft('Velia: договорились проверить маршруты',source(),[first]);assert.deepEqual(draft.memory.entities,[project]);
  assert.deepEqual(memoryDraft('Veliant работает отдельно',source(),[first]).memory.entities,[]);
  assert.deepEqual(memoryDraft('По проекту Velia решили закончить веб-версию',source()).memory.entities,[project]);
  assert.equal(memoryDraft('Предпочитаю русский язык',source()).memory.type,'preference');
});

test('retrieval excludes unrelated projects, disabled facts and disabled account memory',()=>{
  const a=entry('a','Для Velia решили закончить веб-версию',[project]),b=entry('b','Redo запускает розыгрыш',[{kind:'project',name:'Redo'}]);
  assert.deepEqual(recallMemory([a,b],'Что мы решили по проекту Velia?').map(e=>e.id),['a']);
  assert.deepEqual(recallMemory([a,b],'Что мы решили по проекту Geckoshi?'),[]);
  assert.deepEqual(recallMemory([{...a,memory:{...a.memory,enabled:false}},b],'Velia'),[]);
  assert.deepEqual(recallMemory([a,{id:MEMORY_SETTINGS_ID,enabled:false}],'Velia'),[]);
});

test('memory syncs encrypted, edits and deletion propagate, and account switching cannot expose records',async()=>{
  const f=fixture();await f.book.unlock('a'.repeat(24));
  const original=entry('m1','Личный бюджет Velia',[project]);await f.book.save(original);
  await f.book.save({kind:'note',title:'Заметка',content:'Останется после удаления памяти',tags:[]});
  assert.doesNotMatch(JSON.stringify(f.records.get('a')),/Личный|Velia|Исходная/);
  const other=f.other();await other.unlock('a'.repeat(24));assert.equal((await other.load())[1].content,original.content);
  assert.equal((await other.find('')).length,1,'notebook search does not expose memory settings as notes');
  const stale=(await other.load()).find(e=>e.id==='m1');
  await f.book.save({...original,content:'Уточнённый бюджет'});await other.load(true);
  await assert.rejects(other.save({...stale,content:'Устаревшее изменение'},{expectedUpdated:stale.updated}),e=>e.status===409);
  await f.book.remove('m1');assert.equal(recallMemory(await other.load(true),'Velia').length,0);
  f.setUser('b');await other.unlock('b'.repeat(24));assert.deepEqual(await other.load(),[]);
  f.setUser('a');await assert.rejects(other.load(true),/Аккаунт сменился/);assert.equal(other.account,null);
});

test('recall uses only a fresh bounded snapshot and validates every quote and reference',async()=>{
  const values=[entry('m1','Для Velia решили закончить веб-версию',[project])];let requests=0,refresh=false;
  const result=await recallMemoryAnswer({notebook:{load:async force=>{refresh=force;return values;}},query:'Что мы решили по Velia?',model:'velia-quantum',signal:new AbortController().signal,
    request:async(path,payload)=>{requests++;assert.equal(path,'memory/plan');assert.equal(payload.model,'velia-quantum');assert.equal(payload.web_search,false);const snapshot=JSON.parse(payload.messages[1].content);assert.equal(snapshot.records.length,1);assert.ok(!JSON.stringify(payload).includes('encryption_key'));return stream({items:[{id:'M1',quote:'решили закончить веб-версию'}]});}});
  assert.equal(refresh,true);assert.equal(requests,1);assert.deepEqual(result.ids,['m1']);assert.match(result.text,/решили закончить/);assert.match(result.text,/Источник/);
  values.length=0;
  const removed=await recallMemoryAnswer({notebook:{load:async()=>values},query:'Velia',model:'velia-flash',signal:new AbortController().signal,request:async()=>{throw Error('must not call');}});assert.deepEqual(removed.ids,[]);assert.doesNotMatch(removed.text,/закончить/);
});

test('invented model quotes, invalid IDs and truncated responses fall back to actual saved text',async()=>{
  for(const [value,finish] of [[{items:[{id:'M1',quote:'Перевела деньги инвестору'}]},'stop'],[{items:[{id:'made-up',quote:'Velia'}]},'stop'],[{items:[{id:'M1',quote:'Velia'}]},'length'],['<!DOCTYPE html>private error','stop']]){
    const snapshot=[entry('a','Velia использует бесплатную маршрутизацию',[project])];
    const result=await recallMemoryAnswer({notebook:{load:async()=>snapshot},query:'Velia',model:'velia-pro',signal:new AbortController().signal,request:async()=>stream(value,finish)});
    assert.deepEqual(result.ids,['a']);assert.match(result.text,/бесплатную маршрутизацию/);assert.doesNotMatch(result.text,/Перевела|made-up|DOCTYPE|private error/);
  }
});

test('disabled memory, cancellation and account changes stop before publishing a recall result',async()=>{
  const disabled=await recallMemoryAnswer({notebook:{load:async()=>[{id:MEMORY_SETTINGS_ID,enabled:false}]},query:'Velia',model:'velia-flash',signal:new AbortController().signal,request:async()=>{throw Error('must not call');}});assert.match(disabled.text,/выключено/);
  const controller=new AbortController();controller.abort();
  await assert.rejects(recallMemoryAnswer({notebook:{load:async()=>[]},query:'Velia',signal:controller.signal}),{name:'AbortError'});
  await assert.rejects(recallMemoryAnswer({notebook:{load:async()=>[entry('a','Velia',[project])]},query:'Velia',signal:new AbortController().signal,guard:()=>{throw new DOMException('Account changed','AbortError');}}),{name:'AbortError'});
});

test('deletion or corrections on another device during model selection cannot publish a stale fact',async()=>{
  for(const change of ['delete','edit','disable']){
    const original=entry('a','Velia использует бесплатную маршрутизацию',[project]);let reads=0;
    const result=await recallMemoryAnswer({notebook:{load:async()=>{
      if(!reads++)return [original];
      return change==='delete'?[]:change==='edit'?[{...original,content:'Уточнённый факт'}]:[{...original,memory:{...original.memory,enabled:false}}];
    }},query:'Velia',model:'velia-flash',signal:new AbortController().signal,request:async()=>stream({items:[{id:'M1',quote:'бесплатную маршрутизацию'}]})});
    assert.equal(reads,2);assert.deepEqual(result.ids,[]);assert.doesNotMatch(result.text,/бесплатную маршрутизацию/);assert.match(result.text,/изменилась/);
  }
});

test('recall sends bounded matching fragments, including facts near the end of long records',async()=>{
  const values=Array.from({length:12},(_,i)=>entry('m'+i,'Обсуждение. '.repeat(250)+'Velia: бесплатная маршрутизация',[project]));
  values.push({kind:'note',id:'private-note',title:'Скрытая заметка',content:'never-send-private-note'});
  const result=await recallMemoryAnswer({notebook:{load:async()=>values},query:'Velia маршрутизация '+'.'.repeat(2000),model:'velia-flash',signal:new AbortController().signal,
    request:async(_path,payload)=>{const snapshot=JSON.parse(payload.messages[1].content);assert.equal(snapshot.records.length,4);assert.ok(snapshot.question.length<=1000);assert.ok(snapshot.records.every(r=>r.text.length<=600&&r.text.includes('маршрутизация')));assert.ok(JSON.stringify(payload).length<8000);assert.doesNotMatch(JSON.stringify(payload),/never-send-private-note/);return stream({items:[{id:'M1',quote:'бесплатная маршрутизация'}]});}});
  assert.equal(result.ids.length,1);assert.match(result.text,/бесплатная маршрутизация/);
});
