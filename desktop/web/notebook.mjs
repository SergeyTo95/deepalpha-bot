/** Encrypted account notebook. The server stores ciphertext and a revision only. */
const encode=bytes=>{let value='';for(const byte of new Uint8Array(bytes))value+=String.fromCharCode(byte);return btoa(value);};
const decode=value=>Uint8Array.from(atob(value),char=>char.charCodeAt(0));
const clone=value=>JSON.parse(JSON.stringify(value));
const AAD=account=>new TextEncoder().encode('velia-notebook-v1:'+account);
export function validateEntry(entry) {
  if(!entry||!['note','list','favorite','place'].includes(entry.kind)||typeof entry.id!=='string'||entry.id.length>100
      ||typeof entry.title!=='string'||!entry.title.trim()||entry.title.length>200||typeof entry.content!=='string'||entry.content.length>50000
      ||!Array.isArray(entry.tags)||entry.tags.length>12||entry.tags.some(tag=>typeof tag!=='string'||tag.length>40))throw Error('Проверь название и текст заметки.');
  if(entry.kind==='list'&&(!Array.isArray(entry.items)||entry.items.length>200||entry.items.some(item=>!item||typeof item.text!=='string'||!item.text.trim()||item.text.length>500||typeof item.done!=='boolean')))throw Error('Проверь пункты списка.');
  if(entry.kind==='place'&&(!Number.isFinite(entry.lat)||!Number.isFinite(entry.lon)||Math.abs(entry.lat)>90||Math.abs(entry.lon)>180))throw Error('Проверь координаты места.');
  return entry;
}
export class Notebook {
  constructor(request,cryptoAPI=globalThis.crypto){this.request=request;this.crypto=cryptoAPI;this.epoch=0;this.tail=Promise.resolve();this.lock();}
  lock(){this.epoch++;this.controller?.abort();this.controller=new AbortController();this.account=null;this.key=null;this.entries=null;this.revision=0;this.owner=null;this.loading=null;}
  async unlock(account){
    if(this.account===account)return;
    this.lock();
    if(!/^[a-f0-9]{24}$/.test(account))throw Error('Войди в Велию заново.');
    this.account=account;
  }
  async load(force=false){
    if(!this.account)throw Error('Войди в Велию, чтобы открыть заметки.');
    if(force)await this.tail.catch(()=>{});
    if(this.entries&&!force)return clone(this.entries);
    if(this.loading&&!force)return this.loading;
    const {account,epoch}=this;
    const task=(async()=>{
      const result=await this.request('notebook',{signal:this.controller.signal});let entries=[];
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      if(result.account!==account){this.lock();throw Error('Аккаунт сменился. Обнови страницу.');}
      if(!/^[a-f0-9]{64}$/.test(result.encryption_key||'')||typeof result.owner!=='string')throw Error('Не удалось открыть заметки.');
      const bytes=Uint8Array.from(result.encryption_key.match(/../g),value=>parseInt(value,16));
      const key=await this.crypto.subtle.importKey('raw',bytes,'AES-GCM',false,['encrypt','decrypt']);bytes.fill(0);
      if(result.record){
        const bytes=await this.crypto.subtle.decrypt({name:'AES-GCM',iv:decode(result.record.iv),additionalData:AAD(result.owner)},key,decode(result.record.data));
        const value=JSON.parse(new TextDecoder().decode(bytes));
        if(!value||value.version!==1||!Array.isArray(value.entries)||value.entries.length>500)throw Error('Не удалось открыть заметки.');
        entries=value.entries.map(validateEntry);
      }
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      this.key=key;this.entries=entries;this.revision=result.revision;this.owner=result.owner;return clone(entries);
    })();this.loading=task;
    try{return await task;}finally{if(this.loading===task)this.loading=null;}
  }
  mutate(change){
    const epoch=this.epoch;
    const job=async()=>{
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      const entries=await this.load();change(entries);
      if(entries.length>500)throw Error('В блокноте уже 500 записей. Удали ненужные.');
      entries.forEach(validateEntry);
      const bytes=new TextEncoder().encode(JSON.stringify({version:1,entries}));
      if(bytes.length>524288)throw Error('Блокнот заполнен. Удали ненужные записи.');
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      const {key,owner}=this,iv=this.crypto.getRandomValues(new Uint8Array(12));
      const data=await this.crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:AAD(owner)},key,bytes);
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      let result;
      try{result=await this.request('notebook',{method:'PUT',signal:this.controller.signal,data:{account:this.account,owner:this.owner,revision:this.revision,record:{iv:encode(iv),data:encode(data),version:1}}});}
      catch(error){if(error.status===409&&epoch===this.epoch)this.entries=null;throw error;}
      if(epoch!==this.epoch)throw new DOMException('Account changed','AbortError');
      this.entries=entries;this.revision=result.revision;return clone(entries);
    };
    const task=this.tail.catch(()=>{}).then(job);this.tail=task;return task;
  }
  async save(value,{ifAbsent=false}={}){
    const id=value.id||this.crypto.randomUUID();
    await this.mutate(entries=>{const index=entries.findIndex(entry=>entry.id===id);if(index>=0&&ifAbsent)return;const record=validateEntry({content:'',tags:[],...value,id,updated:Date.now()});if(index<0)entries.unshift(record);else entries[index]=record;});
    return id;
  }
  setDone(id,index,done){return this.mutate(entries=>{const entry=entries.find(entry=>entry.id===id);if(entry?.kind==='list'&&entry.items[index]){entry.items[index].done=Boolean(done);entry.updated=Date.now();}});}
  remove(id){return this.mutate(entries=>{const index=entries.findIndex(entry=>entry.id===id);if(index>=0)entries.splice(index,1);});}
  async find(query=''){const entries=await this.load();const terms=query.toLocaleLowerCase().split(/\s+/).filter(Boolean);return entries.filter(entry=>terms.every(term=>[entry.title,entry.content,...entry.tags,...(entry.items||[]).map(item=>item.text)].join(' ').toLocaleLowerCase().includes(term)));}
}
export function notebookCommand(text){
  let match=text.match(/^(?:сохрани|запиши|создай)\s+(?:заметку|идею)(?:\s*[:—-]\s*|\s+)([\s\S]+)$/iu);
  if(match)return {kind:'note',content:match[1].trim()};
  match=text.match(/^(?:создай|сохрани)\s+(список|чек-лист)([^:\n]*):\s*([\s\S]+)$/iu);
  if(match)return {kind:'list',title:('Список'+match[2]).trim(),items:match[3].split(/\n|[,;]/).map(value=>value.replace(/^\s*[-*]\s*/,'').trim()).filter(Boolean).map(value=>({text:value,done:false}))};
  match=text.match(/^(?:найди|покажи)\s+(?:(?:мои|мою)\s+)?(?:заметк[иу]|иде[ию])(?:\s+(?:про|о|по))?\s*[:—-]?\s*(.*)$/iu);
  if(match)return {kind:'find',query:match[1].trim()};
  if(/^(?:сохрани\s+(?:это|последний ответ)|добавь\s+(?:это\s+)?в избранное)[.!]?$/iu.test(text))return {kind:'favorite'};
  match=text.match(/^(?:найди|покажи)\s+на карте\s*[:—-]?\s+(.+)$/iu);
  if(match)return {kind:'map',query:match[1].trim()};
  if(/^открой\s+(?:карту|заметки)[.!]?$/iu.test(text))return {kind:/карту/iu.test(text)?'map':'open'};
  return null;
}
