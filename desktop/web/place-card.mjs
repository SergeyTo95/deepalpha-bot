import {placeTimeline,addPlaceNote,EVENT_LABELS} from './place-history.mjs';
import {NotebookMaterials} from './notebook-materials.mjs';
import {VISIT_STATUSES} from './visits.mjs';
import {fileSize} from './files.mjs';
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
const button=(text,action)=>{const b=node('button',text);b.type='button';b.onclick=action;return b;};
export async function mountPlaceCard(parent,{notebook,placeId,signal,toast,previewFile,open,discuss}){
  const files=new NotebookMaterials(notebook),root=node('section',null,'place-card'),head=node('div'),form=node('form',null,'feature-form'),timeline=node('div',null,'place-timeline');let currentPlace,ticket=0;
  const text=node('textarea');text.maxLength=4000;text.rows=3;text.placeholder='Заметка или договорённость по этому месту';text.setAttribute('aria-label','Запись по месту');text.required=true;
  const type=node('select');type.setAttribute('aria-label','Тип записи по месту');for(const [value,label] of Object.entries(EVENT_LABELS)){const option=node('option',label);option.value=value;type.append(option);}
  const save=node('button','Добавить запись');save.type='submit';form.append(type,text,save);
  form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await addPlaceNote(notebook,placeId,text.value,type.value);if(signal.aborted)return;text.value='';toast('Запись добавлена к месту.');await draw();}catch(error){if(!signal.aborted)toast(error.message);}finally{if(!signal.aborted)save.disabled=false;}};
  const picker=node('input');picker.type='file';picker.multiple=true;picker.accept='.jpg,.jpeg,.png,.webp,.pdf,.docx,.txt,.md,.csv,.json';picker.hidden=true;picker.setAttribute('aria-label','Материалы места');
  const upload=button('Прикрепить фото или документ',()=>picker.click());
  picker.onchange=async()=>{const selected=[...picker.files];picker.value='';if(selected.length>4){toast('Выбери до четырёх файлов за один раз.');return;}upload.disabled=true;let saved=0;try{for(const file of selected){await files.attach(placeId,file);saved++;if(signal.aborted)return;}toast('Материалы сохранены: '+saved+'.');}catch(error){if(!signal.aborted)toast('Сохранено: '+saved+'. '+error.message);}finally{if(!signal.aborted){upload.disabled=false;await draw();}}};
  const links=node('div',null,'feature-actions'),existing=node('select');existing.setAttribute('aria-label','Связать существующую заметку');
  const link=button('Связать заметку',async()=>{link.disabled=true;try{const id=existing.value;await notebook.mutate(entries=>{const value=entries.find(p=>p.id===id&&p.kind==='note'&&!p.placeId);if(!value||!entries.some(p=>p.id===placeId&&p.kind==='place'))throw Error('Запись изменилась. Обнови карточку.');value.placeId=placeId;value.updated=Date.now();});if(!signal.aborted)await draw();}catch(error){if(!signal.aborted)toast(error.message);}finally{if(!signal.aborted)link.disabled=false;}});links.append(existing,link);
  root.append(head,form,upload,picker,node('p','Фото и документы до 5 МБ каждый. Незавершённую загрузку можно проверить кнопкой «Открыть» или удалить и повторить.','notebook-hint'),links,node('h3','История места'),button('Обновить историю',()=>draw(true)),timeline);parent.replaceChildren(root);
  async function readMaterial(id){try{const file=await files.read(id);if(signal.aborted)return;await previewFile(file);await draw();}catch(error){if(!signal.aborted)toast(error.message);}}
  async function draw(refresh=false){const current=++ticket;try{
    const entries=await notebook.load(refresh);if(signal.aborted||ticket!==current)return;currentPlace=entries.find(p=>p.kind==='place'&&p.id===placeId);if(!currentPlace){root.replaceChildren(node('p','Место удалено. Связанные записи остаются в заметках.'));return;}
    head.replaceChildren(node('h2',currentPlace.title),node('p',currentPlace.address||'', 'notebook-hint'));if(currentPlace.content)head.append(node('p',currentPlace.content));
    const actions=node('div',null,'feature-actions');actions.append(button('На карте',()=>open('map',currentPlace)),button('Обсудить с Велией',()=>discuss({...currentPlace,content:[currentPlace.content,...placeTimeline(entries,currentPlace).slice(0,12).map(p=>p.kind==='visit'?[p.date,p.time,p.task,VISIT_STATUSES[p.status],p.result].filter(Boolean).join(' · '):p.kind==='material'?'Файл: '+p.title+' · '+p.content:(EVENT_LABELS[p.eventType]||'Заметка')+': '+p.content)].filter(Boolean).join('\n').slice(0,12000)})));head.append(actions);
    const notes=entries.filter(p=>p.kind==='note'&&!p.placeId);existing.replaceChildren();for(const value of notes){const option=node('option',value.title);option.value=value.id;existing.append(option);}links.hidden=!notes.length;
    timeline.replaceChildren();const rows=placeTimeline(entries,currentPlace);if(!rows.length)timeline.append(node('p','Здесь появятся заметки, договорённости, файлы и результаты визитов.','feature-empty'));
    for(const value of rows){const card=node('article',null,'feature-card place-history-item');card.dataset.entryId=value.id;
      if(value.kind==='visit'){card.append(node('small',value.date+' · '+(value.time||'Без времени')+' · '+value.timezone),node('strong',VISIT_STATUSES[value.status]),node('p',value.task));if(value.result)card.append(node('p','Результат: '+value.result));card.append(button('Открыть визиты',()=>open('map',{tab:'visits',date:value.date})));}
      else{
        card.append(node('small',value.kind==='material'?'Материал · '+fileSize(value.bytes)+' · '+(value.uploadStatus==='ready'?'Сохранён':'Загрузка не завершена'):EVENT_LABELS[value.eventType]||'Заметка'),node('strong',value.title));if(value.content)card.append(node('p',value.content));
        const actions=node('div',null,'feature-actions');if(value.kind==='material')actions.append(button('Открыть',()=>readMaterial(value.id)));else actions.append(button('Изменить',()=>{const edit=node('form',null,'feature-form'),input=node('textarea');input.value=value.content;input.maxLength=4000;input.required=true;const commit=node('button','Сохранить');commit.type='submit';edit.append(input,commit,button('Отмена',()=>draw()));edit.onsubmit=async event=>{event.preventDefault();commit.disabled=true;try{await notebook.mutate(values=>{const entry=values.find(p=>p.id===value.id&&p.placeId===placeId);if(!entry)throw Error('Запись удалена.');entry.content=input.value.trim();entry.title=entry.content.split('\n')[0].slice(0,100);entry.updated=Date.now();});if(!signal.aborted)await draw();}catch(error){if(!signal.aborted)toast(error.message);}finally{commit.disabled=false;}};card.replaceChildren(edit);}));
        actions.append(button('Удалить',async()=>{if(!confirm('Удалить «'+value.title+'»?'))return;try{await notebook.remove(value.id);if(!signal.aborted)await draw();}catch(error){if(!signal.aborted)toast(error.message);}}));card.append(actions);
      }
      timeline.append(card);
    }
  }catch(error){if(!signal.aborted&&ticket===current)timeline.replaceChildren(node('p',error.message,'feature-notice'));}}
  await draw();
}
