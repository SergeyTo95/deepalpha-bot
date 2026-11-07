/** Calendar dates and local appointment times. No implicit UTC conversion. */
export const VISIT_STATUSES={planned:'Запланирован',done:'Выполнен',cancelled:'Отменён'};
export function deviceTimeZone(){try{return Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch{return 'UTC';}}
export function validTimeZone(zone){if(typeof zone!=='string'||zone.length>100)return false;try{new Intl.DateTimeFormat('en',{timeZone:zone}).format(0);return true;}catch{return false;}}
export function validDate(value){if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;const date=new Date(value+'T12:00:00Z');return !Number.isNaN(date.valueOf())&&date.toISOString().slice(0,10)===value&&value>='1900-01-01'&&value<='2199-12-31';}
export function localDate(now=Date.now(),zone=deviceTimeZone()){
  if(!validTimeZone(zone))throw Error('Проверь часовой пояс визита.');
  const fields=Object.fromEntries(new Intl.DateTimeFormat('en',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(now).map(p=>[p.type,p.value]));
  return fields.year+'-'+fields.month+'-'+fields.day;
}
export function visitDate(value,{now=Date.now(),zone=deviceTimeZone()}={}){
  if(typeof value!=='string')throw Error('Укажи дату визита.');
  const relative={today:0,'сегодня':0,tomorrow:1,'завтра':1,day_after_tomorrow:2,'послезавтра':2};
  if(Object.hasOwn(relative,value)){const date=new Date(localDate(now,zone)+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+relative[value]);return date.toISOString().slice(0,10);}
  if(!validDate(value))throw Error('Укажи дату визита: сегодня, завтра или ГГГГ-ММ-ДД.');return value;
}
export function validateVisit(visit){
  if(!visit||visit.kind!=='visit'||!validDate(visit.date)||typeof visit.time!=='string'||visit.time!==''&&!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(visit.time)||!validTimeZone(visit.timezone)||!Object.hasOwn(VISIT_STATUSES,visit.status)
      ||typeof visit.task!=='string'||!visit.task.trim()||visit.task.length>2000||typeof visit.result!=='string'||visit.result.length>4000
      ||typeof visit.address!=='string'||visit.address.length>1000||visit.placeId!==undefined&&(typeof visit.placeId!=='string'||visit.placeId.length>100)
      ||!Number.isFinite(visit.lat)||!Number.isFinite(visit.lon)||Math.abs(visit.lat)>90||Math.abs(visit.lon)>180)throw Error('Проверь место, задачу, дату и время визита.');
  return visit;
}
export function visitsForDate(entries,date,status='all'){
  if(!validDate(date)||status!=='all'&&!Object.hasOwn(VISIT_STATUSES,status))throw Error('Проверь дату и статус выездов.');
  return entries.filter(p=>p.kind==='visit'&&p.date===date&&(status==='all'||p.status===status)).sort((a,b)=>(a.time||'99:99').localeCompare(b.time||'99:99')||a.title.localeCompare(b.title));
}
export function visitConflicts(value,entries){return value.status==='planned'&&value.time?entries.filter(p=>p.kind==='visit'&&p.id!==value.id&&p.status==='planned'&&p.date===value.date&&p.time===value.time&&p.timezone===value.timezone):[];}
export function visitSummary(value){return [value.date,value.time||'Время не назначено',value.title,value.task,VISIT_STATUSES[value.status],value.result].filter(Boolean).join(' · ');}
export function validVisitPlan(plan,places){
  if(!plan||!validDate(plan.date)||!validTimeZone(plan.timezone)||!['all',...Object.keys(VISIT_STATUSES)].includes(plan.status)||!Number.isInteger(plan.total)||plan.total<0||plan.total>500||!Array.isArray(plan.rows)||plan.rows.length>20||plan.total<plan.rows.length)return false;
  try{
    for(const row of plan.rows){const place=places.find(p=>p.ref===row.placeRef);if(!place||typeof row.ref!=='string'||row.ref.length>100||typeof row.visitId!=='string'||row.visitId.length>100||typeof row.title!=='string'||!row.title||row.title.length>200||row.date!==plan.date)return false;validateVisit({...row,kind:'visit',lat:place.lat,lon:place.lon});}
    return true;
  }catch{return false;}
}
