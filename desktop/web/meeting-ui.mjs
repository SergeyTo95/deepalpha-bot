import {crmRequest} from './crm.mjs';
import {meetingReport,meetingEmail,saveMeetingTask} from './meetings.mjs';

const node=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
async function chooseContact({signal,guard}){
  const dialog=node('dialog'),heading=node('h2','Кому сохранить поручение?'),search=node('input'),list=node('select'),status=node('p'),submit=node('button','Выбрать'),cancel=node('button','Отмена');
  dialog.className='file-dialog';search.placeholder='Имя, компания или email';search.setAttribute('aria-label','Поиск контакта CRM');list.setAttribute('aria-label','Выбери точный контакт CRM');submit.type=cancel.type='button';submit.disabled=true;
  const actions=node('div');actions.className='feature-actions';actions.append(submit,cancel);dialog.append(heading,search,list,status,actions);document.body.append(dialog);
  let ticket=0,timeout;
  const load=async()=>{
    const epoch=++ticket;submit.disabled=true;
    try{
      const data=await crmRequest('query',{query:{view:'contacts',search:search.value.trim()||undefined,limit:50}},signal);guard();
      if(epoch!==ticket||!dialog.open)return;
      list.replaceChildren();const empty=node('option','Выбери контакт');empty.value='';list.append(empty);
      for(const contact of data.result.contacts){const option=node('option',contact.name+(contact.company?' · '+contact.company:'')+(contact.email?' · '+contact.email:''));option.value=contact.id;list.append(option);}
      status.textContent=data.result.more?'Показаны первые 50 контактов. Уточни поиск.':data.result.total?'':'Контактов не найдено. Сначала добавь нужного человека в CRM.';
    }catch(error){if(epoch===ticket&&dialog.open)status.textContent=error.message;}
  };
  search.oninput=()=>{clearTimeout(timeout);ticket++;submit.disabled=true;timeout=setTimeout(load,250);};list.onchange=()=>{submit.disabled=!list.value;};
  return new Promise(resolve=>{
    let selected=null;
    const abort=()=>dialog.close();signal.addEventListener('abort',abort,{once:true});
    dialog.addEventListener('close',()=>{ticket++;clearTimeout(timeout);signal.removeEventListener('abort',abort);dialog.remove();resolve(selected);},{once:true});
    submit.onclick=()=>{if(list.value){selected=list.value;dialog.close();}};cancel.onclick=()=>dialog.close();
    dialog.showModal();if(signal.aborted)abort();else load();
  });
}

export function meetingControls(meeting,{notebook,openPulse,chatId,guard,toast,persist,controller,run,downloadText,onDraft}){
  const box=node('section'),bar=node('div');box.className='chat-meeting-result';bar.className='feature-actions';
  const button=(label,action)=>{const b=node('button',label);b.type='button';b.onclick=()=>run(async()=>{guard();await action();guard();}).catch(error=>toast(error.name==='AbortError'?'Действие остановлено.':error.message));return b;};
  const details=node('details'),summary=node('summary','Расшифровка и проверка источника'),source=node('pre',meeting.source);details.append(summary,source);
  bar.append(button('Сохранить протокол',async()=>{
    const content=meetingReport(meeting);if(content.length>50000)throw Error('Протокол слишком большой для одной заметки. Скачай его файлом.');
    if(!window.confirm('Сохранить проверяемый черновик протокола в заметки?\n'+meeting.title))return;
    guard();await notebook.save({id:'meeting:'+meeting.id,kind:'note',title:meeting.title,content,tags:['Встречи']},{ifAbsent:true});guard();toast('Протокол сохранён в заметки.');
  }),button('Скачать расшифровку',async()=>{downloadText(meeting.source,'VELIA-meeting-transcript.txt');}),button('Черновик письма',async()=>{onDraft(meetingEmail(meeting));}));
  box.append(bar,details);
  meeting.actions.forEach((task,index)=>{
    const row=node('div'),label=node('p',`${index+1}. ${task.text}`);row.className='feature-card';
    const actions=node('div');actions.className='feature-actions';
    const receipt=meeting.taskReceipts[index];
    const crm=button(receipt?.state==='saved'?'В CRM сохранено':receipt?'Проверь сохранение в CRM':'Сохранить в CRM',async()=>{
      if(meeting.taskReceipts[index])throw Error('Поручение уже передавалось в CRM. Проверь выбранную карточку.');
      const id=await chooseContact({signal:controller.signal,guard});guard();if(!id)return;
      const saved=await saveMeetingTask({meeting,index,contactId:id,signal:controller.signal,guard,persist});
      if(saved)toast('Поручение сохранено в CRM. Напоминание создаётся отдельно.');
    });crm.disabled=Boolean(receipt);
    actions.append(crm,button('Напомнить',async()=>{await openPulse({title:task.text,resource:{kind:'chat',id:chatId},...(task.due_date?{schedule:{kind:'once',date:task.due_date,time:''}}:{})});}));
    row.append(label,actions);box.append(row);
  });
  return box;
}
