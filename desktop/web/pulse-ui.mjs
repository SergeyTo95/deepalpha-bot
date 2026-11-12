import {mountWebPush} from './web-push.mjs';
import {localParts,pulseWhen,pulseTimeZone} from './pulse.mjs';
import {localDate} from './visits.mjs';
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
const button=(text,fn)=>{const b=node('button',text);b.type='button';b.onclick=fn;return b;};
const labels={active:'Запланировано',waiting:'Ждёт предыдущий шаг',done:'Выполнено',cancelled:'Отменено'};
export function reminderForm(parent,{pulse,toast,signal,draft={},onSaved=()=>{}}){
  const form=node('form',null,'feature-form pulse-editor'),id=crypto.randomUUID();
  const field=(label,type,value)=>{const l=node('label',label),input=node(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=value??'';l.append(input);form.append(l);return input;};
  const title=field('О чём напомнить','textarea',draft.title);title.required=true;title.maxLength=500;title.rows=2;
  const repeat=node('select');repeat.setAttribute('aria-label','Повторение');for(const [value,text] of [['once','Один раз'],['daily','Каждый день'],['weekly','Каждую неделю'],['after','После другого напоминания']]){const option=node('option',text);option.value=value;repeat.append(option);}repeat.value=draft.after_id?'after':draft.schedule?.kind||'once';form.append(repeat);
  const date=field('Дата','date',draft.schedule?.date||localDate());
  const time=field('Время','time',draft.schedule?.time||'');
  const weekday=node('select');weekday.setAttribute('aria-label','День недели');['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'].forEach((label,i)=>{const option=node('option',label);option.value=i;weekday.append(option);});weekday.value=draft.schedule?.weekdays?.[0]??0;form.append(weekday);
  const parentSelect=node('select');parentSelect.setAttribute('aria-label','Предыдущий шаг');for(const item of pulse.state().items.filter(p=>['active','waiting'].includes(p.state))){const option=node('option',item.title);option.value=item.item_id;parentSelect.append(option);}if(draft.after_id)parentSelect.value=draft.after_id;form.append(parentSelect);
  const delay=field('Минут после выполнения предыдущего шага','number',draft.after_minutes??0);delay.min=0;delay.max=10080;
  const tz=field('Часовой пояс','text',draft.timezone||pulseTimeZone());tz.required=true;
  const follow=field('Повторить один раз, если не выполнено · минут, 0 — без повтора','number',draft.followup_minutes||0);follow.min=0;follow.max=10080;
  const quietLabel=node('label','Учитывать тихие часы'),quiet=node('input');quiet.type='checkbox';quiet.checked=draft.respect_quiet!==false;quietLabel.prepend(quiet);form.append(quietLabel);
  const advanced=node('details');advanced.append(node('summary','Дополнительные настройки'),tz.parentElement,follow.parentElement,quietLabel);form.append(node('small','Время по часовому поясу '+tz.value),advanced);const save=node('button','Сохранить напоминание');save.type='submit';form.append(save);
  function toggle(){date.parentElement.hidden=repeat.value!=='once';date.required=repeat.value==='once';time.parentElement.hidden=repeat.value==='after';time.required=repeat.value!=='after';weekday.hidden=repeat.value!=='weekly';parentSelect.hidden=delay.parentElement.hidden=repeat.value!=='after';parentSelect.required=repeat.value==='after';}repeat.onchange=toggle;toggle();
  form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{
    const kind=repeat.value,schedule=kind==='once'?{kind,date:date.value,time:time.value}:kind==='weekly'?{kind,time:time.value,weekdays:[Number(weekday.value)]}:{kind,time:time.value};
    const value={title:title.value.trim(),timezone:tz.value.trim(),schedule,resource:draft.resource||null,followup_minutes:Number(follow.value),respect_quiet:quiet.checked,...(kind==='after'?{after_id:parentSelect.value,after_minutes:Number(delay.value)}:{})};
    const item=await pulse.create(value,id);if(signal.aborted)return;toast('Напоминание сохранено.');onSaved(item);
  }catch(error){if(!signal.aborted)toast(error.message);}finally{if(!signal.aborted)save.disabled=false;}};parent.append(form);return form;
}
export async function mountPulse(parent,{pulse,webPush,notebook,toast,signal,draft={},openResource=()=>{},openVisits=()=>{}}){
  const toolbar=node('div',null,'feature-actions'),editor=node('div'),cards=node('div',null,'pulse-cards'),overview=node('section',null,'pulse-overview'),inbox=node('div'),settingsBox=node('details');
  const deviceBox=node('section',null,'feature-card');mountWebPush(deviceBox,{webPush,signal});
  const settingsTitle=node('summary','Доставка и тихие часы');settingsBox.append(settingsTitle);
  const filter=node('select');filter.setAttribute('aria-label','Показать напоминания');for(const [v,t] of [['active','Предстоящие и незавершённые'],['done','Выполненные'],['cancelled','Отменённые']]){const o=node('option',t);o.value=v;filter.append(o);}
  toolbar.append(button('Добавить напоминание',()=>edit()),button('Обновить',()=>load()),filter);parent.append(overview,deviceBox,toolbar,editor,node('h2','Напоминания'),cards,inbox,settingsBox);let editing=false,settingsDrawn=false;
  function edit(value={}){editing=true;editor.replaceChildren();reminderForm(editor,{pulse,toast,signal,draft:value,onSaved:()=>{editing=false;editor.replaceChildren();load();}});editor.append(button('Закрыть форму',()=>{editing=false;editor.replaceChildren();}));}
  async function operation(fn){try{await fn();if(!signal.aborted)await load();}catch(error){if(!signal.aborted)toast(error.message);}}
  function draw(){if(signal.aborted||parent.contains(document.activeElement)&&['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;const data=pulse.state(),items=data.items||[],notifications=data.notifications||[];
    overview.replaceChildren(node('h2','Пульс · сегодня'));
    const today=localDate(Date.now(),data.settings?.timezone||pulseTimeZone()),due=items.filter(p=>p.state==='active'&&p.action_at&&localDate(p.action_at*1000,data.settings?.timezone||pulseTimeZone())<=today);
    overview.append(node('p','Дел, требующих внимания: '+due.length+'. Отправленное уведомление не означает, что дело выполнено.'));
    if(!data.worker_enabled||!data.worker_last_tick||Date.now()/1000-data.worker_last_tick>120)overview.append(node('p','Доставка сейчас не подтверждена. Напоминания сохраняются; обнови Пульс перед проверкой.','feature-notice'));
    cards.replaceChildren();const visible=items.filter(p=>filter.value==='active'?['active','waiting'].includes(p.state):p.state===filter.value);
    if(!visible.length)cards.append(node('p','Здесь пока нет напоминаний. Можно сказать в чате: «Напомни завтра в 10 позвонить клиенту».','feature-empty'));
    for(const item of visible){const card=node('article',null,'feature-card pulse-card');card.dataset.pulseId=item.item_id;card.append(node('small',labels[item.state]),node('h3',item.title),node('p',pulseWhen(item)));
      if(item.schedule.kind==='daily')card.append(node('small','Ежедневно в '+item.schedule.time));if(item.schedule.kind==='weekly')card.append(node('small','Еженедельно в '+item.schedule.time));
      if(item.notify_at&&item.due_at&&item.notify_at>item.due_at)card.append(node('small','Тихие часы: уведомление '+new Date(item.notify_at*1000).toLocaleString('ru',{timeZone:data.settings?.timezone||item.timezone})));
      const done=item.result||notifications.find(n=>n.item_id===item.item_id&&n.status==='done'&&n.result)?.result;if(done)card.append(node('p','Результат: '+done));
      const actions=node('div',null,'feature-actions');if(item.resource)actions.append(button('Открыть связанное',()=>openResource(item.resource)));
      if(item.state==='active'){
        const result=node('textarea');result.rows=1;result.maxLength=2000;result.placeholder='Результат · необязательно';result.setAttribute('aria-label','Результат выполнения');card.append(result);
        actions.append(button(item.schedule.kind==='daily'||item.schedule.kind==='weekly'?'Выполнено в этот раз':'Выполнено',()=>operation(()=>pulse.act(item,'complete',{result:result.value}))));
        const later=node('details');later.append(node('summary','Позже'));actions.append(later);
        for(const [label,minutes] of [['Через 15 минут',15],['Через час',60]])later.append(button(label,()=>operation(()=>pulse.act(item,'snooze',{...localParts(Math.ceil((Date.now()+minutes*60000)/60000)*60000,item.timezone),timezone:item.timezone}))));
        later.append(button('Перенести',()=>{const form=node('form',null,'feature-form'),date=node('input'),time=node('input');date.type='date';time.type='time';date.required=time.required=true;const parts=localParts(Date.now()+3600000,item.timezone);date.value=parts.date;time.value=parts.time;date.setAttribute('aria-label','Новая дата');time.setAttribute('aria-label','Новое время');const save=node('button','Перенести');save.type='submit';form.append(date,time,save);form.onsubmit=event=>{event.preventDefault();operation(()=>pulse.act(item,'snooze',{date:date.value,time:time.value,timezone:item.timezone}));};card.append(form);}));
      }
      const more=node('details');more.append(node('summary','Ещё'));actions.append(more);
      if(['active','waiting'].includes(item.state))more.append(button('Отменить',()=>operation(()=>pulse.act(item,'cancel'))));
      more.append(button('Удалить',()=>{if(confirm('Удалить напоминание и его уведомления?'))operation(()=>pulse.remove(item.item_id));}));card.append(actions);cards.append(card);
    }
    inbox.replaceChildren(node('h2','Уведомления'));for(const notice of notifications.slice(0,30)){const card=node('article',null,'pulse-notice');card.append(node('strong',notice.title),node('small',(notice.kind==='followup'?'Повтор · ':notice.kind==='digest'?'Обзор · ':'')+({unread:'Не прочитано',read:'Прочитано',done:'Дело выполнено',snoozed:'Перенесено',superseded:'Предыдущий срок'})[notice.status]));if(notice.kind==='task'){card.append(button(notice.task_status==='completed'?'Открыть результат':'Открыть задачу',()=>operation(async()=>{await pulse.read(notice.id);await openResource(notice.resource);})));if(['failed','unknown'].includes(notice.delivery))card.append(node('small','Результат сохранён. Внешняя доставка уведомления не подтверждена.'));}if(notice.kind!=='task'&&notice.delivery!=='off')card.append(node('small','Telegram: '+({pending:'В очереди',sending:'Отправляется',sent:'Отправлено',failed:'Не отправлено',unknown:'Доставка не подтверждена'})[notice.delivery]));if(notice.status==='unread')card.append(button('Прочитано',()=>operation(()=>pulse.read(notice.id))));inbox.append(card);}
    const telegramToggle=settingsBox.querySelector('[data-pulse-telegram]');if(telegramToggle)telegramToggle.disabled=!data.telegram_available;
    if(!settingsDrawn&&data.settings){settingsDrawn=true;drawSettings(data);}
  }
  function drawSettings(data){const prefs=data.settings,form=node('form',null,'feature-form'),inputs={};
    for(const [key,label,type] of [['timezone','Часовой пояс','text'],['quiet','Тихие часы','checkbox'],['quiet_start','Начало тихих часов','time'],['quiet_end','Конец тихих часов','time'],['digest','Утренний обзор напоминаний','checkbox'],['digest_time','Время обзора','time'],['telegram','Уведомления в Telegram','checkbox'],['preview','Показывать содержание в уведомлениях','checkbox']]){const l=node('label',label),input=node('input');input.type=type;if(type==='checkbox')input.checked=prefs[key];else input.value=prefs[key];if(key==='telegram'){input.dataset.pulseTelegram='1';input.disabled=!data.telegram_available;}l.append(input);form.append(l);inputs[key]=input;}
    if(data.telegram_username){const link=node('a','Открыть бота Велии');link.href='https://t.me/'+encodeURIComponent(data.telegram_username);link.target='_blank';link.rel='noopener noreferrer';form.append(link);}form.append(node('p','В Велии напоминания видны в карточках. Для доставки с закрытым сайтом включи уведомления на этом устройстве или Telegram. Содержание скрыто по умолчанию.','notebook-hint'));
    const save=node('button','Сохранить настройки');save.type='submit';form.append(save);form.onsubmit=async event=>{event.preventDefault();save.disabled=true;await operation(()=>pulse.settings(Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.type==='checkbox'?input.checked:input.value]))));save.disabled=false;};settingsBox.append(form);
  }
  async function load(){try{await pulse.sync();if(signal.aborted)return;draw();}catch(error){if(!signal.aborted)cards.replaceChildren(node('p',error.message,'feature-notice'));}}
  const onBlur=()=>queueMicrotask(draw);parent.addEventListener('focusout',onBlur);signal.addEventListener('abort',()=>parent.removeEventListener('focusout',onBlur),{once:true});
  const unsubscribe=pulse.subscribe(draw);signal.addEventListener('abort',unsubscribe,{once:true});filter.onchange=draw;await load();if(signal.aborted)return;
  if(draft.notification_id){
    const notice=pulse.state().notifications.find(n=>n.id===draft.notification_id),item=pulse.state().items.find(p=>p.item_id===notice?.item_id);
    if(item){filter.value=['done','cancelled'].includes(item.state)?item.state:'active';draw();
      const card=[...cards.children].find(p=>p.dataset.pulseId===item.item_id);if(card){card.tabIndex=-1;card.focus({preventScroll:true});card.scrollIntoView?.({block:'center'});}}
    else inbox.scrollIntoView?.({block:'start'});
  }
  if(draft.title||draft.schedule||draft.resource)edit(draft);
  try{const entries=await notebook.load();if(signal.aborted)return;const today=localDate(),visits=entries.filter(p=>p.kind==='visit'&&p.date===today&&p.status==='planned');if(visits.length){const link=button('Выезды сегодня · '+visits.length,()=>openVisits(today));overview.append(link);}}catch{}
}
