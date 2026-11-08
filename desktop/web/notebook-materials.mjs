import {prepareFile} from './files.mjs';
import {MATERIAL_LIMIT,isMaterialId,validatePlaceLink,belongsToPlace} from './place-history.mjs';
const encode=value=>{const bytes=new Uint8Array(value),parts=[];for(let i=0;i<bytes.length;i+=32768)parts.push(String.fromCharCode(...bytes.subarray(i,i+32768)));return btoa(parts.join(''));};
const decode=text=>{const raw=atob(text),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return bytes;};
const aad=(owner,id)=>new TextEncoder().encode('velia-notebook-material-v1:'+owner+':'+id);
export class NotebookMaterials{
  constructor(notebook){this.notebook=notebook;}
  async context(){const book=this.notebook,epoch=book.epoch;await book.load();const account=book.account,owner=book.owner,key=book.key;const guard=()=>{if(!account||book.epoch!==epoch||book.account!==account)throw new DOMException('Account changed','AbortError');};guard();return {book,account,owner,key,guard};}
  async attach(placeId,input,{caption='',visitId}={}){
    const file=prepareFile(input);if(file.size>MATERIAL_LIMIT)throw Error('Для карточки места выбери файл до 5 МБ.');
    if(typeof caption!=='string'||caption.length>4000)throw Error('Подпись к файлу — до 4000 символов.');
    const {book,account,owner,key,guard}=await this.context();
    const id=book.crypto.randomUUID(),meta={id,kind:'material',title:file.name.slice(0,200),content:caption,tags:[],placeId,mime:file.type,bytes:file.size,uploadStatus:'pending',created:Date.now(),updated:Date.now(),...(visitId?{visitId}:{})};validatePlaceLink(meta);
    await book.mutate(entries=>{if(!entries.some(p=>p.id===placeId&&p.kind==='place'))throw Error('Место удалено. Обнови карточку.');if(visitId&&!entries.some(p=>p.id===visitId&&p.kind==='visit'&&belongsToPlace(p,entries.find(place=>place.id===placeId&&place.kind==='place'))))throw Error('Выбери визит этого места.');entries.unshift(meta);});guard();
    const bytes=await file.arrayBuffer();guard();const iv=book.crypto.getRandomValues(new Uint8Array(12));const data=await book.crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:aad(owner,id)},key,bytes);guard();
    await book.request('notebook/materials/'+id,{method:'PUT',signal:book.controller.signal,data:{account,owner,record:{version:1,iv:encode(iv),data:encode(data)}}});guard();
    await book.mutate(entries=>{const entry=entries.find(p=>p.id===id&&p.kind==='material');if(!entry)throw Error('Запись материала удалена.');entry.uploadStatus='ready';entry.updated=Date.now();});guard();return id;
  }
  async read(id){
    if(!isMaterialId(id))throw Error('Материал не найден.');const {book,key,owner,guard}=await this.context();
    const entry=(await book.load()).find(p=>p.id===id&&p.kind==='material');guard();if(!entry)throw Error('Материал удалён. Обнови карточку.');
    const result=await book.request('notebook/materials/'+id,{signal:book.controller.signal});guard();
    if(result.owner!==owner)throw Error('Аккаунт сменился. Обнови страницу.');
    const record=result.record;if(!record||record.version!==1||typeof record.iv!=='string'||record.iv.length!==16||typeof record.data!=='string'||record.data.length>7000000)throw Error('Не удалось проверить материал.');
    const bytes=await book.crypto.subtle.decrypt({name:'AES-GCM',iv:decode(record.iv),additionalData:aad(owner,id)},key,decode(record.data));guard();if(bytes.byteLength!==entry.bytes)throw Error('Размер материала не совпадает с записью.');
    if(entry.uploadStatus==='pending')await book.mutate(entries=>{const value=entries.find(p=>p.id===id);if(value)value.uploadStatus='ready';});guard();
    return new File([bytes],entry.title,{type:entry.mime,lastModified:entry.created||entry.updated});
  }
}
