/** Account-owned place links; model-visible history contains metadata, never file bytes. */
export const MATERIAL_LIMIT=5*1024*1024;
export const MATERIAL_TYPES=['image/jpeg','image/png','image/webp','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','text/plain'];
export const EVENT_LABELS={note:'Заметка',agreement:'Договорённость'};
export function validatePlaceLink(value){
  if(value.placeId!==undefined&&(typeof value.placeId!=='string'||!value.placeId||value.placeId.length>100))throw Error('Не удалось определить место записи.');
  if(value.eventType!==undefined&&!Object.hasOwn(EVENT_LABELS,value.eventType))throw Error('Проверь тип записи.');
  if(value.kind==='material'&&(!value.placeId||!MATERIAL_TYPES.includes(value.mime)||!Number.isInteger(value.bytes)||value.bytes<1||value.bytes>MATERIAL_LIMIT||!['pending','ready'].includes(value.uploadStatus)||!isMaterialId(value.id)||value.visitId!==undefined&&(typeof value.visitId!=='string'||value.visitId.length>100)))throw Error('Проверь материал и связанное место.');
  return value;
}
export const isMaterialId=id=>typeof id==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(id);
export function belongsToPlace(entry,place){
  if(entry.placeId)return entry.placeId===place.id;
  return entry.kind==='visit'&&entry.title===place.title&&Math.abs(entry.lat-place.lat)<0.000001&&Math.abs(entry.lon-place.lon)<0.000001;
}
export function placeTimeline(entries,place){return entries.filter(p=>['note','visit','material'].includes(p.kind)&&belongsToPlace(p,place)).sort((a,b)=>{
  const date=p=>p.kind==='visit'?p.date+'T'+(p.time||'00:00'):new Date(p.created||p.updated||0).toISOString();return date(b).localeCompare(date(a));
});}
export function publicPlaceHistory(entries,place){
  const rows=placeTimeline(entries,place);
  return {title:place.title,address:(place.address||place.content||'').slice(0,1000),comment:place.content.slice(0,2000),total:rows.length,shown:Math.min(rows.length,12),items:rows.slice(0,12).map(p=>p.kind==='visit'?{kind:p.kind,title:p.title,date:p.date,time:p.time,timezone:p.timezone,task:p.task.slice(0,400),status:p.status,result:p.result.slice(0,400)}:p.kind==='material'?{kind:p.kind,name:p.title,mime:p.mime,bytes:p.bytes,upload_status:p.uploadStatus,caption:p.content.slice(0,400),created:p.created||p.updated}:{kind:p.kind,type:p.eventType||'note',text:p.content.slice(0,800),created:p.created||p.updated})};
}
export async function ensureSavedPlace(notebook,point){
  const entries=await notebook.load();
  const found=point.savedId?entries.find(p=>p.kind==='place'&&p.id===point.savedId):entries.find(p=>p.kind==='place'&&p.lat===point.lat&&p.lon===point.lon&&p.title===point.title);
  if(found)return found;
  if(point.savedId)throw Error('Место удалено. Найди или выбери его заново.');
  const place={id:crypto.randomUUID(),kind:'place',title:point.title,address:point.address||'',content:point.comment||'',category:point.category||'',country_code:point.country_code||'',tags:[],lat:point.lat,lon:point.lon,created:Date.now()};
  await notebook.save(place);point.savedId=place.id;return place;
}
export async function addPlaceNote(notebook,placeId,content,eventType='note'){
  if(typeof content!=='string'||!content.trim()||content.length>4000||!Object.hasOwn(EVENT_LABELS,eventType))throw Error('Запиши текст заметки или договорённости, до 4000 символов.');
  const id=crypto.randomUUID();await notebook.mutate(entries=>{
    if(!entries.some(p=>p.kind==='place'&&p.id===placeId))throw Error('Место удалено. Обнови карточку.');
    entries.unshift({id,kind:'note',title:content.trim().split('\n')[0].slice(0,100),content:content.trim(),tags:[],placeId,eventType,created:Date.now(),updated:Date.now()});
  });return id;
}
