export const MEMORY_TYPES=Object.freeze({fact:'Факт',decision:'Решение',task:'Поручение',preference:'Предпочтение'});
export const ENTITY_TYPES=Object.freeze({project:'Проект',person:'Человек',topic:'Тема'});
export const MEMORY_SETTINGS_ID='memory:settings';
const text=(value,max)=>typeof value==='string'&&value.trim().length>0&&value.length<=max;
const time=value=>Number.isSafeInteger(value)&&value>0&&value<=8640000000000000;
export const entityKey=entity=>entity.kind+':'+entity.name.normalize('NFKC').trim().toLocaleLowerCase().replace(/ё/gu,'е').replace(/\s+/gu,' ');
export function validateMemory(entry){
  if(entry.kind==='memory-settings'){
    if(entry.id!==MEMORY_SETTINGS_ID||typeof entry.enabled!=='boolean')throw Error('Не удалось проверить настройки памяти.');
    return entry;
  }
  const m=entry.memory,s=m?.source;
  if(!m||!Object.hasOwn(MEMORY_TYPES,m.type)||typeof m.enabled!=='boolean'||!time(m.createdAt)||!text(entry.content,4000)||
    !Array.isArray(m.entities)||m.entities.length>8||m.entities.some(e=>!e||!Object.hasOwn(ENTITY_TYPES,e.kind)||!text(e.name,80))||
    new Set(m.entities.map(entityKey)).size!==m.entities.length||!s||!['chat','note','manual'].includes(s.kind)||
    !text(s.id,100)||!text(s.title,200)||!text(s.quote,4000)||!time(s.at)||!['user','assistant','manual'].includes(s.role)||
    (s.kind==='chat'&&!text(s.messageId,128)))throw Error('Проверь текст, связи и источник записи памяти.');
  return entry;
}
