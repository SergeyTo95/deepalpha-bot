import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook} from '../notebook.mjs';
import {NotebookMaterials} from '../notebook-materials.mjs';
import {addPlaceNote,placeTimeline,publicPlaceHistory} from '../place-history.mjs';
import {runMapAgent,wantsMap,wantsPlaceHistory,validMapData,mapMessageContext} from '../map-agent.mjs';
const account='a'.repeat(24),point={id:'place-client',kind:'place',title:'Клиент Иван',address:'Анталия, 1',content:'Заходить со двора',lat:36.9,lon:30.7,tags:[]};
function fixture(){
  let snapshot={revision:0,record:null},failUpload=false;const materials=new Map();
  const request=async(path,options={})=>{
    if(path.startsWith('notebook/materials/')){const id=path.split('/').at(-1);if(options.method==='PUT'){if(failUpload)throw Error('Нет сети');assert.equal(options.data.account,account);materials.set(id,options.data.record);return {owner:'7',saved:true};}if(options.method==='DELETE'){materials.delete(id);return {owner:'7',deleted:true};}if(!materials.has(id))throw Object.assign(Error('Загрузка не завершена'),{status:404});return {owner:'7',record:materials.get(id)};}
    if(options.method==='PUT'){assert.equal(options.data.revision,snapshot.revision);snapshot={revision:snapshot.revision+1,record:options.data.record};return {revision:snapshot.revision};}
    return {account,owner:'7',encryption_key:'1'.repeat(64),...snapshot};
  };
  return {book:new Notebook(request,webcrypto),reopen:()=>new Notebook(request,webcrypto),materials,fail:()=>failUpload=true};
}
const plan=object=>new Response('data: '+JSON.stringify({choices:[{delta:{content:JSON.stringify(object)},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'Content-Type':'text/event-stream'}});
test('place history keeps agreements, visit results and materials separate from other places',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);await addPlaceNote(f.book,point.id,'Оплата по пятницам','agreement');
  await f.book.save({kind:'note',id:'private-note',title:'Личное',content:'Не связано с этим клиентом',tags:[]});
  const rows=placeTimeline(await f.book.load(),point);assert.equal(rows.length,1);assert.equal(rows[0].eventType,'agreement');
  assert.ok(!JSON.stringify(publicPlaceHistory(await f.book.load(),point)).includes('Не связано'));
});
test('original material bytes reopen on another device, are encrypted, and deletion removes the blob',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);const files=new NotebookMaterials(f.book),original=new File(['signed private act'], 'act.txt',{type:'text/plain'});
  const id=await files.attach(point.id,original,{caption:'Акт за ноябрь'});assert.ok(!JSON.stringify([...f.materials]).includes('signed private act'));
  const second=f.reopen();await second.unlock(account);const reopened=await new NotebookMaterials(second).read(id);assert.equal(await reopened.text(),'signed private act');assert.equal(reopened.name,'act.txt');
  await second.remove(id);assert.equal(f.materials.size,0);assert.ok(!(await second.load()).some(p=>p.id===id));
});
test('an interrupted upload remains visibly pending and forged/swapped ciphertext cannot be opened',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);const files=new NotebookMaterials(f.book),file=new File(['private content'], 'test.txt',{type:'text/plain'});
  const first=await files.attach(point.id,file),second=await files.attach(point.id,file);
  f.materials.set(second,f.materials.get(first));await assert.rejects(files.read(second));
  f.fail();await assert.rejects(files.attach(point.id,file),/Нет сети/);const pending=(await f.book.load()).find(p=>p.uploadStatus==='pending');assert.ok(pending);await assert.rejects(files.read(pending.id),/не завершена/);
  await f.book.remove(pending.id);assert.ok(!(await f.book.load()).some(p=>p.id===pending.id));
});
test('unsupported/oversized files and missing place relations cannot create a material',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);const files=new NotebookMaterials(f.book);
  await assert.rejects(files.attach(point.id,new File(['<svg/>'],'x.svg',{type:'image/svg+xml'})));
  await assert.rejects(files.attach(point.id,new File([new Uint8Array(5*1024*1024+1)],'x.pdf',{type:'application/pdf'})),/5 МБ/);
  await assert.rejects(files.attach('another-owner-place',new File(['x'],'x.txt',{type:'text/plain'})),/Место удалено/);assert.equal(f.materials.size,0);
});
for(const model of ['velia-flash','velia-quantum','velia-pro'])test(model+' writes a requested agreement, reads only its place history, and attaches actual supplied files',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);await f.book.save({id:'foreign-note',kind:'note',title:'Другое',content:'private unrelated note',tags:[]});const known={ref:'p-client',savedId:point.id,title:point.title,address:point.address,lat:point.lat,lon:point.lon};
  async function run(content,steps,files=[]){const user={role:'user',content,requestId:crypto.randomUUID()};return runMapAgent({model,user,history:[{role:'assistant',content:'Показала место.',mapData:{version:1,places:[known],display:[known.ref],route:null}},user],files,notebook:f.book,signal:new AbortController().signal,request:async(path,payload)=>{assert.equal(payload.model,model);const next=steps.shift();assert.ok(next);return plan(next);}});}
  const first=await run('Запиши договорённость по этому месту: оплата по пятницам',[{tool:'add_place_note',arguments:{place_id:known.ref,text:'Оплата по пятницам',type:'agreement'}},{answer:'Договорённость записана.'}]);assert.ok(validMapData(first.data));assert.equal(first.data.placeHistory.total,1);
  const second=await run('Покажи историю этого места',[{tool:'get_place_history',arguments:{place_id:known.ref}},{answer:'Оплата по пятницам.'}]);assert.ok(!mapMessageContext({role:'assistant',content:second.text,mapData:second.data}).includes('private unrelated note'));
  const third=await run('Прикрепи эти файлы к этому месту',[{tool:'attach_place_materials',arguments:{place_id:known.ref,caption:'Акт'}},{answer:'Файл сохранён в карточке места.'}],[new File(['actual-file-content'],'act.txt',{type:'text/plain'})]);assert.equal(third.data.placeHistory.total,2);assert.equal(f.materials.size,1);
});
test('history intent is routed from chat and an invented place or missing file cannot claim success',async()=>{
  assert.equal(wantsPlaceHistory('Сохрани заметку: позвонить маме',[{role:'assistant',mapData:{version:1}}]),false);
  assert.ok(wantsMap('Покажи историю места клиента'));assert.ok(wantsMap('Запиши договорённость по этому месту: звонить заранее'));
  const f=fixture();await f.book.unlock(account);await f.book.save(point);const user={role:'user',content:'Прикрепи файлы к этому месту',requestId:crypto.randomUUID()},steps=[{tool:'attach_place_materials',arguments:{place_id:'fabricated'}},{answer:'Сохранила файлы.'}];
  const result=await runMapAgent({model:'velia-flash',user,history:[user],notebook:f.book,signal:new AbortController().signal,request:async()=>plan(steps.shift())});assert.ok(result.text.includes('не подтверждена'));assert.equal(f.materials.size,0);
});

test('a pending metadata write recovers the stored original, while logout blocks a late file response',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);const files=new NotebookMaterials(f.book),id=await files.attach(point.id,new File(['recover me'],'act.txt',{type:'text/plain'}));
  await f.book.mutate(entries=>{entries.find(p=>p.id===id).uploadStatus='pending';});
  assert.equal(await (await files.read(id)).text(),'recover me');assert.equal((await f.book.load()).find(p=>p.id===id).uploadStatus,'ready');
  const original=f.book.request;let finish,started;
  const waiting=new Promise(resolve=>started=resolve);
  f.book.request=async(path,options)=>{if(path==='notebook/materials/'+id){started();return new Promise(resolve=>finish=resolve);}return original(path,options);};
  const pending=files.read(id);await waiting;f.book.lock();finish({owner:'7',record:f.materials.get(id)});await assert.rejects(pending,{name:'AbortError'});assert.equal(f.book.key,null);
});


test('opening the already selected saved place reads real history without a planner call',async()=>{
  const f=fixture();await f.book.unlock(account);await f.book.save(point);await addPlaceNote(f.book,point.id,'Оплата по пятницам','agreement');
  const user={role:'user',content:'Покажи историю этого места',requestId:crypto.randomUUID()},known={ref:'p-client',savedId:point.id,title:point.title,address:point.address,lat:point.lat,lon:point.lon};
  const result=await runMapAgent({model:'velia-flash',user,history:[{role:'assistant',content:'Выбрано место',mapData:{version:1,places:[known],display:[known.ref],route:null}},user],notebook:f.book,signal:new AbortController().signal,request:async()=>{throw Error('Simple reads must not call a model');}});
  assert.equal(result.text,'Показала историю места.');assert.equal(result.data.placeHistory.items[0].text,'Оплата по пятницам');
});
