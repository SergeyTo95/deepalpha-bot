import {VISIT_STATUSES,deviceTimeZone,localDate,validateVisit,visitsForDate,visitConflicts} from './visits.mjs';
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
const button=(text,action)=>{const b=node('button',text);b.type='button';b.onclick=action;return b;};
export function editVisit(parent,value,{notebook,signal,toast,onDone}){
  const form=node('form',null,'feature-form visit-editor');
  const field=(label,type,initial,max)=>{const wrap=node('label',label),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=initial??'';if(max)input.maxLength=max;wrap.append(input);form.append(wrap);return input;};
  const title=field('Место','text',value.title,200);title.required=true;
  const address=field('Адрес или ориентир','text',value.address||value.content,1000);
  const task=field('Что нужно сделать','textarea',value.task,2000);task.required=true;task.rows=3;
  const date=field('Дата','date',value.date||localDate());date.required=true;
  const time=field('Время · можно оставить пустым','time',value.time);
  const zone=field('Часовой пояс','text',value.timezone||deviceTimeZone(),100);zone.required=true;
  const wrap=node('label','Статус'),status=node('select');for(const [id,label] of Object.entries(VISIT_STATUSES)){const option=node('option',label);option.value=id;status.append(option);}status.value=value.status||'planned';wrap.append(status);form.append(wrap);
  const result=field('Результат визита','textarea',value.result,4000);result.rows=3;
  const save=node('button','Сохранить визит');save.type='submit';
  const actions=node('div',null,'feature-actions');actions.append(save,button('Отмена',()=>onDone(false)));form.append(actions);
  form.onsubmit=async event=>{
    event.preventDefault();save.disabled=true;
    try{
      const visit={content:'',tags:[],...value,kind:'visit',title:title.value.trim(),address:address.value.trim(),task:task.value.trim(),date:date.value,time:time.value,timezone:zone.value.trim(),status:status.value,result:result.value};validateVisit(visit);
      await notebook.save(visit);if(signal.aborted)return;toast('Визит сохранён.');onDone(true);
    }catch(error){if(!signal.aborted)toast(error.message);}finally{if(!signal.aborted)save.disabled=false;}
  };
  parent.replaceChildren(form);task.focus();return form;
}
export async function mountVisits(parent,{notebook,signal,toast,openMap,discuss,remind=()=>{},draft={}}){
  const toolbar=node('div',null,'notebook-toolbar'),date=node('input');date.type='date';date.value=draft.date||localDate();date.setAttribute('aria-label','Дата выездов');
  const status=node('select');status.setAttribute('aria-label','Статус выездов');for(const [id,label] of [['all','Все статусы'],...Object.entries(VISIT_STATUSES)]){const option=node('option',label);option.value=id;status.append(option);}
  const cards=node('div',null,'visit-cards'),editor=node('div');let ticket=0,editing=false;
  const edit=value=>{editing=true;toolbar.hidden=cards.hidden=true;editVisit(editor,value,{notebook,signal,toast,onDone:()=>{editing=false;toolbar.hidden=cards.hidden=false;editor.replaceChildren();draw();}});};
  toolbar.append(date,status,button('Добавить визит',async()=>{
    try{
      const places=(await notebook.load()).filter(p=>p.kind==='place');if(signal.aborted)return;
      if(!places.length){toast('Сначала найди место в чате или сохрани точку на карте.');return;}
      const form=node('form',null,'feature-form'),select=node('select');select.setAttribute('aria-label','Место для визита');for(const p of places){const option=node('option',p.title);option.value=p.id;select.append(option);}
      const submit=node('button','Выбрать место');submit.type='submit';form.append(select,submit);editor.replaceChildren(form);
      form.onsubmit=event=>{event.preventDefault();const p=places.find(p=>p.id===select.value);edit({kind:'visit',title:p.title,address:(p.address||p.content).slice(0,1000),placeId:p.id,lat:p.lat,lon:p.lon,date:date.value,time:'',timezone:deviceTimeZone(),task:'',status:'planned',result:'',content:'',tags:[]});};
    }catch(error){if(!signal.aborted)toast(error.message);}
  }),button('Обновить',()=>draw(true)));
  parent.append(toolbar,node('p','План по местному времени визита. Порядок — по назначенному времени; время дороги не рассчитано. Уведомления пока не отправляются.','notebook-hint'),cards,editor);
  async function draw(refresh=false){
    const current=++ticket;
    try{
      const entries=await notebook.load(refresh),visits=visitsForDate(entries,date.value,status.value);if(signal.aborted||current!==ticket||editing)return;cards.replaceChildren();
      if(!visits.length)cards.append(node('p','На эту дату выездов пока нет. Скажи в чате: «Запланируй визит к этому месту на завтра в 10:00».','feature-empty'));
      for(const value of visits){
        const card=node('article',null,'feature-card visit-card');card.dataset.visitId=value.id;
        card.append(node('small',(value.time||'Без времени')+' · '+VISIT_STATUSES[value.status]),node('h3',value.title),node('p',value.task),node('p',value.address,'notebook-hint'),node('small',value.timezone));
        if(value.result)card.append(node('p','Результат: '+value.result));
        if(visitConflicts(value,entries).length)card.append(node('p','На это же время назначен другой визит.','feature-notice'));
        const actions=node('div',null,'feature-actions');actions.append(button('Изменить',()=>edit(value)),button('На карте',()=>openMap(value)),button('Обсудить с Велией',()=>discuss(value)),button('Напомнить',()=>remind(value)));card.append(actions);cards.append(card);
      }
    }catch(error){if(!signal.aborted&&current===ticket)cards.replaceChildren(node('p',error.message,'feature-notice'));}
  }
  date.onchange=status.onchange=()=>draw();await draw();
}
