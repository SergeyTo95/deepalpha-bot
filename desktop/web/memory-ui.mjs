import {ENTITY_TYPES,MEMORY_TYPES,MEMORY_SETTINGS_ID,entityKey} from './memory-schema.mjs';
import {memoryEnabled,memoryGraph,recallMemory} from './memory.mjs';
const node=(tag,text,className)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(className)e.className=className;return e;};
const button=(text,action)=>{const e=node('button',text);e.type='button';e.onclick=action;return e;};
export async function mountMemory(parent,{notebook,toast,signal,draft={},openSource=()=>{},discuss=()=>{}}){
  const owner=notebook.account,epoch=notebook.epoch;
  const guard=()=>{signal.throwIfAborted();if(notebook.account!==owner||notebook.epoch!==epoch)throw new DOMException('Account changed','AbortError');};
  const alive=()=>!signal.aborted&&notebook.account===owner&&notebook.epoch===epoch;
  const toolbar=node('div',null,'notebook-toolbar'),search=node('input');search.type='search';search.placeholder='Проект, человек или решение';search.setAttribute('aria-label','Поиск в памяти');search.value=draft.query||'';
  const type=node('select');type.setAttribute('aria-label','Тип памяти');
  for(const [value,label] of [['','Все типы'],...Object.entries(MEMORY_TYPES)]){const option=node('option',label);option.value=value;type.append(option);}
  const settings=node('label',null,'memory-toggle'),enabled=node('input');enabled.type='checkbox';settings.append(enabled,node('span','Использовать сохранённую память'));
  const status=node('p',null,'notebook-hint');status.setAttribute('role','status');
  const graph=node('section',null,'memory-connections'),cards=node('div',null,'notebook-cards'),editor=node('div');editor.hidden=true;
  let entity='',ticket=0,editing=false;
  const run=async(action)=>{try{guard();await action();guard();}catch(error){if(alive())toast(error.status===409?'Память изменилась на другом устройстве. Обнови список перед повтором.':error.message);}};
  function edit(value){
    guard();editing=true;clear.disabled=true;toolbar.hidden=graph.hidden=cards.hidden=settings.hidden=true;editor.hidden=false;editor.replaceChildren();
    const form=node('form',null,'feature-form memory-editor');
    const field=(label,tag,value)=>{const l=node('label',label),input=node(tag);input.value=value||'';l.append(input);form.append(l);return input;};
    const title=field('Название','input',value.title);title.maxLength=200;title.required=true;
    const content=field('Что помнить','textarea',value.content);content.rows=7;content.maxLength=4000;content.required=true;
    const kind=field('Тип записи','select','');for(const [key,label] of Object.entries(MEMORY_TYPES)){const option=node('option',label);option.value=key;kind.append(option);}kind.value=value.memory?.type||'fact';
    const entityInputs=Object.fromEntries(Object.entries(ENTITY_TYPES).map(([key,label])=>{const input=field(label+' · через запятую','input',(value.memory?.entities||[]).filter(e=>e.kind===key).map(e=>e.name).join(', '));input.maxLength=640;return [key,input];}));
    form.append(node('p','Связи объединяют записи с одним проектом, человеком или темой. Всего до 8 связей на запись.','notebook-hint'));
    if(value.memory?.source){const source=value.memory.source;const details=node('details');details.append(node('summary','Источник: '+source.title),node('p',source.quote,'memory-quote'));form.append(details);}
    const save=node('button','Сохранить в память');save.type='submit';const actions=node('div',null,'feature-actions');
    const done=()=>{guard();editing=false;clear.disabled=false;toolbar.hidden=graph.hidden=cards.hidden=settings.hidden=false;editor.hidden=true;draw();};
    const cancel=button('Отмена',()=>{if(!save.disabled)done();});actions.append(save,cancel);form.append(actions);editor.append(form);title.focus();
    form.onsubmit=event=>{event.preventDefault();if(save.disabled)return;save.disabled=cancel.disabled=true;run(async()=>{
      const contentValue=content.value.trim(),source=value.memory?.source||{kind:'manual',id:'manual',title:'Добавлено вручную',quote:contentValue,role:'manual',at:Date.now()};
      const entities=[...new Map(Object.entries(entityInputs).flatMap(([kind,input])=>input.value.split(',').map(name=>({kind,name:name.trim()})).filter(e=>e.name).map(e=>[entityKey(e),e]))).values()];
      const entry={...value,kind:'memory',title:title.value.trim(),content:contentValue,tags:[],memory:{...value.memory,type:kind.value,enabled:value.memory?.enabled??true,createdAt:value.memory?.createdAt||Date.now(),entities,source}};
      await notebook.save(entry,{expectedUpdated:value.updated});guard();toast('Сохранила в память.');done();
    }).finally(()=>{if(alive())save.disabled=cancel.disabled=false;});};
  }
  const add=()=>edit({title:'',content:'',tags:[],kind:'memory'});
  toolbar.append(search,type,button('Добавить',add),button('Обновить',()=>draw(true)));
  parent.append(settings,node('p','Сохраняются только записи, которые ты добавил командой «Запомни: …» или кнопкой «В память». Записи синхронизируются через зашифрованный блокнот. Для ответа выбранной модели передаются только найденные фрагменты.','notebook-hint'),toolbar,status,graph,cards,editor);
  const clear=button('Удалить всю память',()=>run(async()=>{
    if(!confirm('Удалить все записи памяти? Заметки и история чатов останутся.'))return;
    guard();await notebook.mutate(entries=>{for(let i=entries.length-1;i>=0;i--)if(entries[i].kind==='memory')entries.splice(i,1);});guard();entity='';await draw();toast('Записи памяти удалены.');
  }));parent.append(node('p','Удаление записи памяти не удаляет исходные сообщения и прежние ответы в чатах. Заметки и память используют общее хранилище: до 500 записей и 512 КБ.','notebook-hint'),clear);
  enabled.onchange=()=>{const wanted=enabled.checked;enabled.disabled=true;run(async()=>{await notebook.save({id:MEMORY_SETTINGS_ID,kind:'memory-settings',title:'Настройки памяти',content:'',tags:[],enabled:wanted});guard();await draw();}).finally(()=>{if(alive())enabled.disabled=false;});};
  async function draw(refresh=false){
    const id=++ticket;
    try{
      const all=await notebook.load(refresh);guard();if(id!==ticket||editing)return;
      enabled.checked=memoryEnabled(all);
      const memories=all.filter(e=>e.kind==='memory');
      status.textContent='Записей: '+memories.length+' · '+(enabled.checked?'память включена':'использование памяти выключено');
      const projection=memoryGraph(memories),entities=projection.nodes.filter(n=>n.kind!=='memory').sort((a,b)=>a.title.localeCompare(b.title));
      graph.replaceChildren(node('h2','Связи'));
      const links=node('div',null,'feature-actions');links.append(button('Все записи',()=>{entity='';draw();}));
      for(const item of entities){const count=projection.edges.filter(e=>e.to===item.id).length,b=button(item.title+' · '+count,()=>{entity=item.id;search.value='';draw();});b.setAttribute('aria-pressed',String(entity===item.id));links.append(b);}graph.append(links);
      if(!entities.length)graph.append(node('p','Добавь проект или человека при редактировании записи — здесь появятся связи.','notebook-hint'));
      const matched=search.value.trim()?recallMemory(all.map(e=>e.kind==='memory'?{...e,memory:{...e.memory,enabled:true}}:e).filter(e=>e.kind!=='memory-settings'),search.value,{limit:500}):memories;
      const values=matched.filter(e=>(!type.value||e.memory.type===type.value)&&(!entity||projection.edges.some(edge=>edge.from===e.id&&edge.to===entity))).sort((a,b)=>(b.updated||0)-(a.updated||0));
      cards.replaceChildren();
      if(!values.length)cards.append(node('p',memories.length?'По этому запросу записей нет.':'Пока ничего не сохранено. Скажи в чате: «Запомни: по проекту Velia решили сначала закончить веб-версию».','feature-empty'));
      for(const entry of values){
        const card=node('article',null,'feature-card memory-card');card.dataset.memoryId=entry.id;
        card.append(node('small',MEMORY_TYPES[entry.memory.type]+(entry.memory.enabled?'':' · не используется')),node('h3',entry.title),node('p',entry.content,'memory-content'));
        const relation=node('div',null,'feature-actions');for(const e of entry.memory.entities)relation.append(button(ENTITY_TYPES[e.kind]+': '+e.name,()=>{entity='entity:'+entityKey(e);search.value='';draw();}));card.append(relation);
        const source=entry.memory.source,details=node('details');details.append(node('summary','Источник: '+source.title+' · '+new Date(source.at).toLocaleString()),node('p',source.quote,'memory-quote'),node('small',source.role==='assistant'?'Сохранено из ответа Велии':source.role==='user'?'Сохранено из сообщения пользователя':'Добавлено вручную'));
        if(source.kind!=='manual')details.append(button('Открыть источник',()=>run(()=>openSource(source))));card.append(details);
        const actions=node('div',null,'feature-actions');
        actions.append(button('Изменить',()=>edit(entry)),button(entry.memory.enabled?'Не учитывать':'Учитывать',()=>run(async()=>{await notebook.save({...entry,memory:{...entry.memory,enabled:!entry.memory.enabled}});guard();await draw();})),button('Обсудить',()=>discuss(entry)),button('Удалить',()=>run(async()=>{if(!confirm('Удалить «'+entry.title+'» из памяти?'))return;guard();await notebook.remove(entry.id);guard();await draw();})));
        card.append(actions);cards.append(card);
      }
    }catch(error){if(alive()&&id===ticket)cards.replaceChildren(node('p',error.message,'feature-notice'));}
  }
  search.oninput=()=>draw();type.onchange=()=>draw();await draw(true);
  if(alive()&&draft.entry)edit(draft.entry);
  else if(alive()&&draft.id){const entry=(await notebook.load()).find(e=>e.id===draft.id&&e.kind==='memory');guard();if(entry)edit(entry);}
}
