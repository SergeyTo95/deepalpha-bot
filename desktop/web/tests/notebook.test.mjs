import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {Notebook,notebookCommand} from '../notebook.mjs';
import {distanceKm,navigationUrl} from '../map.mjs';
function fixture(){
  let user='7';const accounts={'7':'a'.repeat(24),'8':'b'.repeat(24)},records=new Map();
  const request=async(path,options={})=>{
    assert.equal(path,'notebook');const value=records.get(user)||{revision:0,record:null};
    if(options.method==='PUT'){
      if(options.data.owner!==user||options.data.account!==accounts[user])throw Object.assign(Error('owner changed'),{status:403});
      if(options.data.revision!==value.revision)throw Object.assign(Error('revision conflict'),{status:409});
      records.set(user,{revision:value.revision+1,record:options.data.record});return {revision:value.revision+1};
    }
    return {...value,owner:user,account:accounts[user],encryption_key:(user==='7'?'1':'2').repeat(64)};
  };
  return {book:new Notebook(request,webcrypto),second:()=>new Notebook(request,webcrypto),records,accounts,setUser:value=>user=value};
}

test('notes are encrypted, reopen in another browser and isolate account changes',async()=>{
  const f=fixture();await f.book.unlock(f.accounts['7']);const id=await f.book.save({kind:'note',title:'Доставка дронами',content:'Личный план',tags:['Работа']});
  assert.ok(!JSON.stringify(f.records.get('7')).includes('Личный план'));
  const other=f.second();await other.unlock(f.accounts['7']);assert.equal((await other.find('дронами'))[0].id,id);
  f.book.lock();await assert.rejects(f.book.load(),/Войди/);
  f.setUser('8');await f.book.unlock(f.accounts['8']);assert.deepEqual(await f.book.load(),[]);
  await f.book.save({kind:'note',title:'Другой аккаунт',content:'Отдельная запись',tags:[]});
  f.setUser('7');await assert.rejects(f.book.load(true),/Аккаунт сменился/);assert.equal(f.book.key,null);
});

test('stale writes are rejected and independent list toggles retain each other',async()=>{
  const f=fixture();await f.book.unlock(f.accounts['7']);const id=await f.book.save({kind:'list',title:'Покупки',content:'',tags:[],items:[{text:'Хлеб',done:false},{text:'Молоко',done:false}]});
  const other=f.second();await other.unlock(f.accounts['7']);await other.load();
  await Promise.all([f.book.setDone(id,0,true),f.book.setDone(id,1,true)]);
  assert.deepEqual((await f.book.load())[0].items.map(item=>item.done),[true,true]);
  await assert.rejects(other.save({kind:'note',title:'Устаревшая вкладка',content:'x',tags:[]}),/conflict/);
  await other.load(true);assert.deepEqual((await other.load())[0].items.map(item=>item.done),[true,true]);
});

test('modified ciphertext is rejected rather than exposed or silently overwritten',async()=>{
  const f=fixture();await f.book.unlock(f.accounts['7']);await f.book.save({kind:'note',title:'Секрет',content:'Текст',tags:[]});
  const saved=f.records.get('7');saved.record.data=(saved.record.data[0]==='A'?'B':'A')+saved.record.data.slice(1);
  const other=f.second();await other.unlock(f.accounts['7']);await assert.rejects(other.load());assert.equal(other.entries,null);
});

test('a late response after account switching cannot unlock or clear the new account',async()=>{
  let finish;
  const book=new Notebook(()=>new Promise(resolve=>finish=resolve),webcrypto);
  await book.unlock('a'.repeat(24));const pending=book.load();await book.unlock('b'.repeat(24));
  finish({account:'a'.repeat(24),owner:'7',encryption_key:'1'.repeat(64),revision:0,record:null});
  await assert.rejects(pending,{name:'AbortError'});assert.equal(book.account,'b'.repeat(24));assert.equal(book.key,null);
});

test('saving the same answer again preserves edits to an existing favorite',async()=>{
  const f=fixture();await f.book.unlock(f.accounts['7']);
  await f.book.save({id:'favorite:answer',kind:'favorite',title:'Мой заголовок',content:'Дополненный ответ',tags:['Важное']});
  await f.book.save({id:'favorite:answer',kind:'favorite',title:'Исходный ответ',content:'Исходный ответ',tags:[]},{ifAbsent:true});
  assert.equal((await f.book.load())[0].content,'Дополненный ответ');assert.equal((await f.book.load()).length,1);
});

test('explicit notebook commands do not intercept general model requests',()=>{
  assert.equal(notebookCommand('Сохрани заметку: моя идея').kind,'note');
  assert.equal(notebookCommand('Создай список покупок: хлеб, молоко').items.length,2);
  assert.equal(notebookCommand('Найди мою идею про доставку').kind,'find');
  assert.equal(notebookCommand('Найди на карте Анталию').kind,'map');
  assert.equal(notebookCommand('Составь список покупок на неделю'),null);
  assert.equal(notebookCommand('Расскажи как создать заметку'),null);
  assert.equal(notebookCommand('Добавь это в избранное').kind,'favorite');
});

test('map distance and navigation links use actual coordinates and fixed hosts',()=>{
  const a={lat:36.8969,lon:30.7133},b={lat:41.0082,lon:28.9784};
  assert.ok(distanceKm(a,b)>470&&distanceKm(a,b)<490);
  const link=new URL(navigationUrl(b,a));assert.equal(link.origin,'https://www.google.com');assert.equal(link.searchParams.get('api'),'1');assert.equal(link.searchParams.get('destination'),'41.0082,28.9784');
});
