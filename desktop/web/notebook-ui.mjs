import {editVisit} from './visit-ui.mjs';
import {visitSummary} from './visits.mjs';
const node=(tag,text,className)=>{const element=document.createElement(tag);if(text)element.textContent=text;if(className)element.className=className;return element;};
const button=(text,action)=>{const b=node('button',text);b.type='button';b.onclick=action;return b;};
export function editEntry(parent,value,{notebook,toast,signal,onDone}) {
  if(value.kind==='visit')return editVisit(parent,value,{notebook,toast,signal,onDone});
  const form=node('form',null,'feature-form notebook-editor');
  const field=(label,type,content)=>{const l=node('label',label),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=content||'';l.append(input);form.append(l);return input;};
  const title=field('Название','text',value.title);title.required=true;title.maxLength=200;
  const list=value.kind==='list';
  const address=value.kind==='place'?field('Адрес','text',value.address||''):null;if(address)address.maxLength=1000;
  const content=field(list?'Пункты · каждый с новой строки':value.kind==='place'?'Комментарий':'Текст заметки','textarea',list?(value.items||[]).map(item=>(item.done?'[x] ':'[ ] ')+item.text).join('\n'):value.content);
  content.maxLength=50000;content.rows=10;
  const tags=field('Теги · через запятую','text',(value.tags||[]).join(', '));tags.maxLength=450;
  let lat,lon;
  if(value.kind==='place'){lat=field('Широта','number',String(value.lat));lon=field('Долгота','number',String(value.lon));lat.required=lon.required=true;lat.step=lon.step='any';lat.min=-90;lat.max=90;lon.min=-180;lon.max=180;}
  const save=node('button','Сохранить');save.type='submit';
  const actions=node('div',null,'feature-actions');actions.append(save,button('Отмена',()=>onDone(false)));form.append(actions);
  form.onsubmit=async event=>{
    event.preventDefault();save.disabled=true;
    try{
      const entry={...value,title:title.value.trim(),content:list?'':content.value,tags:[...new Set(tags.value.split(',').map(tag=>tag.trim()).filter(Boolean))]};
      if(list)entry.items=content.value.split('\n').map(line=>line.trim()).filter(Boolean).map(line=>({done:/^(?:-\s*)?\[x\]/iu.test(line),text:line.replace(/^(?:-\s*)?\[[ x]\]\s*/iu,'').trim()}));
      if(address)entry.address=address.value.trim();
      if(lat){entry.lat=Number(lat.value);entry.lon=Number(lon.value);}
      await notebook.save(entry);
      if(!signal.aborted){toast('Сохранено.');onDone(true);}
    }catch(error){if(!signal.aborted)toast(error.message);}finally{if(!signal.aborted)save.disabled=false;}
  };
  parent.replaceChildren(form);title.focus();return form;
}
export async function mountNotebook(parent,{notebook,toast,signal,discuss,openMap,openMaterial=()=>{},openMiniApp=()=>{},openPlace=()=>{},remind=()=>{},remember=()=>{},draft={}}) {
  const owner=notebook.account,ownerEpoch=notebook.epoch;
  const launchApp=entry=>{if(!signal.aborted&&notebook.account===owner&&notebook.epoch===ownerEpoch)openMiniApp(entry);};
  const toolbar=node('div',null,'notebook-toolbar'),search=node('input');search.type='search';search.placeholder='Поиск по своим записям';search.setAttribute('aria-label','Поиск заметок');
  const kind=node('select');kind.setAttribute('aria-label','Тип записи');
  for(const [value,title] of [['','Все записи'],['note','Заметки'],['list','Списки'],['favorite','Избранное'],['place','Места'],['visit','Выезды'],['material','Материалы'],['miniapp','Приложения']]){const option=node('option',title);option.value=value;kind.append(option);}
  const cards=node('div',null,'notebook-cards'),editor=node('div');editor.hidden=true;
  const openEditor=entry=>{editing=true;toolbar.hidden=cards.hidden=true;editor.hidden=false;editEntry(editor,entry,{notebook,toast,signal,onDone:()=>{editing=false;toolbar.hidden=cards.hidden=false;editor.hidden=true;draw();}});};
  toolbar.append(search,kind,button('Заметка',()=>openEditor({kind:'note',title:'',content:'',tags:[]})),button('Список',()=>openEditor({kind:'list',title:'Новый список',content:'',tags:[],items:[]})),button('Обновить',()=>draw(true)));
  parent.append(toolbar,node('p','Сохраняй здесь идеи, полезные ответы и места. Записи зашифрованы и доступны после входа на других устройствах.','notebook-hint'),cards,editor);
  let epoch=0,editing=false;
  async function draw(refresh=false){
    const ticket=++epoch;
    try{
      if(refresh)await notebook.load(true);
      const values=(await notebook.find(search.value)).filter(entry=>!kind.value||entry.kind===kind.value).sort((a,b)=>(b.updated||0)-(a.updated||0));
      if(signal.aborted||ticket!==epoch||editing)return;
      cards.replaceChildren();
      if(!values.length)cards.append(node('p','Здесь пока пусто. Создай заметку или скажи в чате: «Сохрани заметку: …».','feature-empty'));
      for(const entry of values){
        const card=node('article',null,'feature-card notebook-card');card.dataset.noteId=entry.id;
        const badge={note:'Заметка',list:'Список',favorite:'Избранное',place:'Место',visit:'Выезд',material:'Материал',miniapp:'Приложение'}[entry.kind];
        card.append(node('small',badge),node('h3',entry.title));
        if(entry.kind==='list'){
          for(const [index,item] of (entry.items||[]).slice(0,8).entries()){
            const label=node('label',null,'notebook-check'),check=node('input');check.type='checkbox';check.checked=item.done;
            label.append(check,node('span',item.text));card.append(label);
            check.onchange=async()=>{try{await notebook.setDone(entry.id,index,check.checked);if(!signal.aborted)draw();}catch(error){if(!signal.aborted){check.checked=!check.checked;toast(error.message);}}};
          }
          if(entry.items.length>8)card.append(node('p','Ещё пунктов: '+(entry.items.length-8)));
        }else card.append(node('p',(entry.kind==='visit'?visitSummary(entry):entry.content).slice(0,240), 'notebook-preview'));
        if(entry.tags.length)card.append(node('p',entry.tags.map(tag=>'#'+tag).join(' '),'notebook-tags'));
        const actions=node('div',null,'feature-actions');actions.append(button('Открыть',()=>entry.kind==='miniapp'?launchApp(entry):entry.kind==='material'?openMaterial(entry):openEditor(entry)));
        if(entry.kind==='place')actions.append(button('История и материалы',()=>openPlace(entry)));
        if(['place','visit'].includes(entry.kind))actions.append(button('На карте',()=>openMap(entry)));
        actions.append(button('Напомнить',()=>remind(entry)));
        actions.append(button('В память',()=>remember(entry)));
        actions.append(button('Обсудить с Велией',()=>discuss(entry)),button('Удалить',async()=>{
          if(!confirm('Удалить «'+entry.title+'»?'))return;
          try{await notebook.remove(entry.id);if(!signal.aborted)draw();}catch(error){if(!signal.aborted)toast(error.message);}
        }));card.append(actions);cards.append(card);
      }
    }catch(error){if(!signal.aborted&&ticket===epoch)cards.replaceChildren(node('p',error.status===409?'Записи изменились на другом устройстве. Нажми «Обновить».':error.message,'feature-notice'));}
  }
  search.oninput=()=>draw();kind.onchange=()=>draw();await draw();if(draft.id&&!signal.aborted){const value=(await notebook.load()).find(p=>p.id===draft.id);if(value){if(value.kind==='miniapp')launchApp(value);else if(value.kind==='material')openMaterial(value);else openEditor(value);}}
}
