import {deviceTimeZone,localDate,visitDate,validateVisit,visitsForDate,visitConflicts} from './visits.mjs';
export const VISIT_ARGS={schedule_visit:['place_id','date','time','timezone','task'],list_visits:['date','status'],update_visit:['visit_id','place_id','date','time','timezone','task','status','result']};
export const VISIT_TOOLS=[
  {name:'schedule_visit',arguments:{place_id:'ref места',date:'today|tomorrow|day_after_tomorrow|YYYY-MM-DD',time:'HH:mm, либо пустая строка если время не назначено',timezone:'IANA, необязательно',task:'что нужно сделать'},description:'Запланировать выезд по явной просьбе. Место должно быть найдено/сохранено, дату и время не выдумывать.'},
  {name:'list_visits',arguments:{date:'today|tomorrow|YYYY-MM-DD',status:'planned|done|cancelled|all, необязательно'},description:'План на день из сохранённых визитов, по назначенному времени. Возвращает visit_id для изменения визита и place_id для карты/навигатора. Максимум 20 записей, total показывает полное количество.'},
  {name:'update_visit',arguments:{visit_id:'ref визита из list_visits/предыдущего плана',date:'необязательно',time:'необязательно HH:mm',timezone:'необязательно IANA',task:'необязательно',status:'planned|done|cancelled, необязательно',result:'необязательно, результат работы',place_id:'необязательно, ref нового места'},description:'Перенести визит, изменить задачу, отметить выполнение/отмену, записать результат. Только по просьбе пользователя и по известному visit_id.'},
];
export function simpleVisitRequest(name,content){
  if(/(?:и|затем|then|and)\s+(?:покажи|построй|проложи|сохрани|найди|отметь|запланируй|отмени|заверши|перенеси|измени|обнови|show|prepare|find|schedule|cancel|complete|reschedule|update)/iu.test(content))return false;
  if(name!=='list_visits'&&/(?:^|[^\p{L}])(?:несколько|оба|обе|все|два|две|три|четыре|кажд[\p{L}]*|both|all|each|two|three)(?=$|[^\p{L}])|\d+\s+(?:визит|выезд|visits?)/iu.test(content))return false;
  if(name!=='list_visits'&&/(?:перв[\p{L}]*|first)[\s\S]{0,50}(?:и|,)\s*(?:втор[\p{L}]*|second)/iu.test(content))return false;
  if(name==='list_visits'&&new Set([...content.matchAll(/сегодня|завтра|today|tomorrow/giu)].map(m=>m[0].toLowerCase())).size>1)return false;
  return true;
}
export function quickVisitReply(name,output,content,language='ru'){
  if(!simpleVisitRequest(name,content))return null;
  const lang=String(language).toLowerCase(),ru=lang.startsWith('ru'),en=lang.startsWith('en');if(!ru&&!en)return null;
  const date=output.plan?.date||output.date;
  if(name==='list_visits'){if(/запланируй|назначь|добавь|внеси|перенеси|измени|обнови|отметь|пометь|заверши|отмени|запиши|schedule|book|reschedule|update|mark|complete|cancel/iu.test(content))return null;return ru?(output.total?'Выездов на '+date+': '+output.total+'. Показала план.':'На '+date+' выездов с выбранным статусом нет.'):'Visits on '+date+': '+output.total+'. The plan is in the chat.';}
  if(!output.saved)return null;
  if(name==='schedule_visit')return ru?(output.already_scheduled?'Этот визит уже есть в плане на '+date+'.':'Запланировала визит на '+date+'. Показала план.'):(output.already_scheduled?'This visit is already in the plan for '+date+'.':'Scheduled the visit for '+date+'. The plan is in the chat.');
  if(name==='update_visit'){
    if(/выполн|заверш|completed|\bdone\b/iu.test(content)&&output.status!=='done'||/отмени|cancel/iu.test(content)&&output.status!=='cancelled'||/результат|итог|result|outcome/iu.test(content)&&!output.changed?.includes('result'))return null;
    return ru?(output.status==='done'?'Отметила визит выполненным.':output.status==='cancelled'?'Отменила визит.':'Обновила визит. Показала план на '+date+'.'):(output.status==='done'?'Marked the visit as completed.':output.status==='cancelled'?'Cancelled the visit.':'Updated the visit. The plan for '+date+' is in the chat.');
  }
  return null;
}
export function createVisitActions({state,notebook,user,guard,findPlace,addPlace,persist,now=Date.now()}){
  const zone=deviceTimeZone(),known=new Map((state.visitPlan?.rows||[]).map(row=>[row.ref,row.visitId]));
  const instructions='План выездов: текущая дата '+localDate(now,zone)+', часовой пояс устройства '+zone+'. Относительные даты передавай today/tomorrow, клиент вычислит календарный день. Не придумывай время: без него time="". Сначала найди/выбери место, затем schedule_visit. «Собери выезды на завтра» — list_visits, не создание новых записей. Для выполнения/переноса используй update_visit с точным ref из плана; если неясно какой визит, сначала list_visits и уточни. Результат записывай по словам пользователя, не сочиняй. Напоминания не отправляются. Сортировка по времени — не оптимизация дороги. После записи дождись успешного результата. Если даты/времени не хватает, уточни.';
  const previous=(state.visitPlan?.rows||[]).slice(0,10).map(row=>({visit_id:row.ref,place_id:row.placeRef,title:row.title.slice(0,100),date:row.date,time:row.time,timezone:row.timezone,status:row.status,task:row.task.slice(0,100)}));
  const publicRow=row=>({visit_id:row.ref,place_id:row.placeRef,title:row.title.slice(0,100),address:row.address.slice(0,120),date:row.date,time:row.time,timezone:row.timezone,status:row.status,task:row.task.slice(0,160),result:row.result.slice(0,160),conflict:row.conflict,text_truncated:row.task.length>160||row.result.length>160||row.title.length>100||row.address.length>120});
  async function dayPlan(date,status='all',refresh=false){
    const values=await notebook.load(refresh);guard();const all=visitsForDate(values,date,status),rows=[];
    for(const value of all.slice(0,20)){
      const place=addPlace({title:value.title,address:value.address,lat:value.lat,lon:value.lon,...(value.placeId&&values.some(p=>p.kind==='place'&&p.id===value.placeId)?{savedId:value.placeId}:{})});
      const previousRef=[...known].find(([,id])=>id===value.id)?.[0],ref=previousRef||'v'+crypto.randomUUID();known.set(ref,value.id);
      rows.push({ref,visitId:value.id,placeRef:place.ref,title:value.title,address:value.address,date:value.date,time:value.time,timezone:value.timezone,task:value.task,status:value.status,result:value.result,conflict:visitConflicts(value,values).length>0});
    }
    state.visitPlan={date,timezone:zone,status,total:all.length,rows};state.display=[...new Set(rows.map(row=>row.placeRef))].slice(0,12);state.route=null;
    return {date,timezone:zone,status,total:all.length,shown:rows.length,visits:rows.map(publicRow)};
  }
  const noWrite=/(?:не\s+(?:надо\s+|нужно\s+)?(?:планир|запланир|назнач|перенос|отмет|отмеч|отмен|заверш|меня|обнов|запис)|(?:do not|don't|never)\s+(?:schedule|book|update|reschedule|cancel|complete|mark))/iu.test(user.content);
  const canSchedule=!noWrite&&/запланируй|назначь|добавь|внеси|schedule|book/iu.test(user.content);
  const canUpdate=!noWrite&&/перенеси|измени|обнови|отметь|пометь|заверши|отмени|запиши|добавь|reschedule|update|mark|complete|cancel/iu.test(user.content);
  async function attempt(key){if(user.visitWrites?.includes(key))throw Error('Изменение визита уже выполнялось. Проверь план и отправь новое поручение.');user.visitWrites=[...(user.visitWrites||[]),key];await persist();guard();}
  async function run(name,args){
    if(name==='list_visits'){state.visitPlan=null;state.display=[];state.route=null;return dayPlan(visitDate(args.date??'today',{now,zone}),args.status||'planned',true);}
    if(name==='schedule_visit'){
      if(!canSchedule)throw Error('Для записи нужна просьба запланировать или добавить визит.');
      const place=findPlace(args.place_id),timezone=args.timezone||zone,date=visitDate(args.date,{now,zone:timezone});
      const value={id:crypto.randomUUID(),kind:'visit',title:place.title,content:'',tags:[],placeId:place.savedId,address:place.address,lat:place.lat,lon:place.lon,date,time:args.time??'',timezone,task:typeof args.task==='string'?args.task.trim():args.task,status:'planned',result:'',updated:Date.now()};validateVisit(value);
      const entries=await notebook.load();guard();
      const duplicate=entries.find(p=>p.kind==='visit'&&p.status!=='cancelled'&&p.date===date&&p.time===value.time&&p.timezone===timezone&&p.task===value.task&&Math.abs(p.lat-value.lat)<0.000001&&Math.abs(p.lon-value.lon)<0.000001);
      if(duplicate)return {saved:true,already_scheduled:true,plan:await dayPlan(date)};
      await attempt('schedule:'+date+':'+value.time+':'+place.ref+':'+value.task);
      let saved=value;
      await notebook.mutate(values=>{const same=values.find(p=>p.kind==='visit'&&p.status!=='cancelled'&&p.date===date&&p.time===value.time&&p.timezone===timezone&&p.task===value.task&&p.lat===value.lat&&p.lon===value.lon);if(same)saved=same;else values.unshift(value);});guard();
      const conflicts=visitConflicts(saved,await notebook.load());guard();
      return {saved:true,conflicts:conflicts.map(p=>({title:p.title,time:p.time})),plan:await dayPlan(date)};
    }
    if(name==='update_visit'){
      if(!canUpdate)throw Error('Для изменения визита нужна явная просьба пользователя.');
      const id=known.get(args.visit_id);if(!id)throw Error('Выбери визит из плана. Сначала открой выезды на нужный день.');
      const entries=await notebook.load();guard();const existing=entries.find(p=>p.id===id&&p.kind==='visit');if(!existing)throw Error('Этот визит удалён. Обнови план.');
      const patch={};for(const key of ['time','task','timezone','status','result'])if(Object.hasOwn(args,key))patch[key]=args[key];
      if(Object.hasOwn(args,'date'))patch.date=visitDate(args.date,{now,zone:patch.timezone||existing.timezone});
      if(args.place_id){const place=findPlace(args.place_id);Object.assign(patch,{placeId:place.savedId,title:place.title,address:place.address,lat:place.lat,lon:place.lon});}
      if(!Object.keys(patch).length)throw Error('Укажи, что нужно изменить у визита.');validateVisit({...existing,...patch});
      await attempt('update:'+id+':'+JSON.stringify(patch));
      let updated;
      await notebook.mutate(values=>{const index=values.findIndex(p=>p.id===id&&p.kind==='visit');if(index<0)throw Error('Визит удалён. Обнови план.');updated=validateVisit({...values[index],...patch,updated:Date.now()});values[index]=updated;});guard();
      return {saved:true,status:updated.status,changed:Object.keys(patch),conflicts:visitConflicts(updated,await notebook.load()).map(p=>({title:p.title,time:p.time})),plan:await dayPlan(updated.date)};
    }
    throw Error('Неизвестное действие с визитом.');
  }
  function quickCommand(){
    const input=user.content.trim();
    if(!/(?:и|затем|then|and)\s+(?:покажи|построй|проложи|сохрани|найди|отметь|запланируй|show|prepare|find|schedule)/iu.test(input)&&!canSchedule&&!canUpdate&&/^(?:а\s+)?(?:собери|покажи|открой|какие|что|show|list|what)/iu.test(input)&&/выезд|визит|план|visits?|appointments?/iu.test(input)){
      const dates=[...input.matchAll(/(?:^|[^\p{L}])(послезавтра|завтра|сегодня|day after tomorrow|tomorrow|today|\d{4}-\d{2}-\d{2})(?=$|[^\p{L}])/giu)].map(m=>m[1].toLowerCase());
      if(dates.length===1){const date=dates[0]==='day after tomorrow'?'day_after_tomorrow':dates[0];return {name:'list_visits',args:{date,status:/выполн|заверш|completed|\bdone\b/iu.test(input)?'done':/отмен|cancelled/iu.test(input)?'cancelled':/все|all/iu.test(input)?'all':'planned'}};}
    }
    if(!canUpdate||!state.visitPlan?.rows.length||!simpleVisitRequest('update_visit',input))return null;
    const status=/(?:отметь|пометь|заверши|mark|complete)[\s\S]*(?:выполн|заверш|completed|\bdone\b)|^(?:заверши|complete)\s/iu.test(input)?'done':/^(?:отмени|cancel)\s/iu.test(input)?'cancelled':null;
    if(!status)return null;let row;
    const ordinal=input.match(/перв(?:ый|ого|ую)|втор(?:ой|ого|ую)|трет(?:ий|ьего|ью)|(?:визит|выезд)\s*(?:№|номер)?\s*(\d+)|\b(first|second|third)\b/iu);
    if(ordinal){if(/(?:визит|выезд)\s+(?:к|по|у|в)\s+[\p{L}]/iu.test(input.split(/результат|итог|result|outcome/iu)[0]))return null;const word=ordinal[0].toLowerCase(),index=ordinal[1]?Number(ordinal[1])-1:/перв|first/iu.test(word)?0:/втор|second/iu.test(word)?1:2;row=state.visitPlan.rows[index];}
    else{const candidates=state.visitPlan.rows.filter(p=>p.task.length>=4&&input.toLocaleLowerCase().includes(p.task.toLocaleLowerCase()));if(candidates.length===1)row=candidates[0];else if(state.visitPlan.rows.length===1&&/^(?:отметь|пометь|заверши|отмени|mark|complete|cancel)\s+(?:(?:этот|данный|the|this)\s+)?(?:визит|выезд|visit)(?:\s+(?:как\s+)?(?:выполненным|заверш[её]нным|отмен[её]нным|completed|done))?(?:[.!]|\s*(?:результат|result)|$)/iu.test(input))row=state.visitPlan.rows[0];}
    if(!row)return null;const result=input.match(/(?:результат|итог|result|outcome)\s*[:—-]\s*([\s\S]+)$/iu);
    return {name:'update_visit',args:{visit_id:row.ref,status,...(result?{result:result[1].trim()}:{})}};
  }
  return {instructions,previous,run,quickCommand};
}
