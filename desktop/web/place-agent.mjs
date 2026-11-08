import {publicPlaceHistory,ensureSavedPlace,addPlaceNote} from './place-history.mjs';
import {NotebookMaterials} from './notebook-materials.mjs';
export const PLACE_ARGS={get_place_history:['place_id'],add_place_note:['place_id','text','type'],attach_place_materials:['place_id','caption','visit_id']};
export const PLACE_TOOLS=[
  {name:'get_place_history',arguments:{place_id:'ref сохранённого места'},description:'Прочитать комментарий, заметки, договорённости, визиты с результатами и названия материалов только выбранного места. Содержимое файлов не читается. Сначала list_places, если ref неизвестен. Не обещать историю несохранённых разговоров.'},
  {name:'add_place_note',arguments:{place_id:'ref выбранного места',text:'текст по словам пользователя, до 4000 символов',type:'note|agreement'},description:'Добавить заметку или договорённость к конкретному месту. Только по явной просьбе записать/сохранить. Если место ещё не сохранено, сохраняется вместе с записью.'},
  {name:'attach_place_materials',arguments:{place_id:'ref места',caption:'необязательная подпись',visit_id:'необязательно, ref визита именно этого места'},description:'Сохранить реально прикреплённые пользователем файлы в карточку места. Не принимает ссылки, пути и придуманные ID. При отсутствии файлов попросить прикрепить. Не анализирует содержимое.'},
];
export function wantsPlaceHistory(text,history=[]){
  const prior=history.filter(m=>m.role==='assistant'&&m.mapData&&!m.failed).at(-1);
  return /(?:истори[яию]|договор[её]нност|материал|заметк).{0,45}(?:мест[аеоу]|объект|адрес)|(?:мест[аеоу]|объект).{0,35}(?:истори|договор[её]нност|материал)/iu.test(text)||Boolean(prior&&/^(?:а\s+)?(?:что.{0,25}(?:обсуждали|договорились)|покажи.{0,20}(?:истори|договор[её]нност|материал)|(?:запиши|сохрани|добавь).{0,65}(?:этому|этого|здесь|сюда|к нему|к ней)|(?:прикрепи|сохрани|добавь).{0,20}(?:файл|фото|документ)|show.{0,20}history|attach.{0,20}(?:files|photos)|save.{0,20}(?:note|agreement))/iu.test(text));
}
export function validPlaceHistory(value,places){return Boolean(value&&typeof value.placeRef==='string'&&places.some(p=>p.ref===value.placeRef&&p.savedId)&&typeof value.title==='string'&&value.title.length<=200&&typeof value.address==='string'&&value.address.length<=1000&&typeof value.comment==='string'&&value.comment.length<=2000&&Number.isInteger(value.total)&&value.total>=0&&value.total<=500&&Array.isArray(value.items)&&value.items.length<=12&&value.items.length<=value.total&&JSON.stringify(value).length<=30000&&value.items.every(p=>p&&['note','visit','material'].includes(p.kind)));}
export function createPlaceActions({state,notebook,user,guard,findPlace,persist,files=[]}){
  const read=async(point,refresh=true)=>{const entries=await notebook.load(refresh);guard();const place=entries.find(p=>p.kind==='place'&&p.id===point.savedId);if(!place)throw Error('Сначала сохрани или выбери сохранённое место.');state.placeHistory={placeRef:point.ref,...publicPlaceHistory(entries,place)};state.display=[point.ref];state.route=null;state.visitPlan=null;return state.placeHistory;};
  async function run(name,args){
    const point=findPlace(args.place_id);
    if(name==='get_place_history')return read(point);
    const denied=/(?:не\s+(?:надо\s+|нужно\s+)?(?:сохран|запис|добав|прикреп)|do not|don't|never)/iu.test(user.content);
    const allowed=name==='add_place_note'?/запиши|сохрани|добавь|save|record|add/iu.test(user.content):/прикрепи|сохрани|добавь|attach|save|add/iu.test(user.content);
    if(denied||!allowed)throw Error('Для записи нужна явная просьба пользователя.');
    if(name==='attach_place_materials'&&(!files.length||files.length>4))throw Error('Прикрепи от одного до четырёх файлов и отправь поручение снова.');
    const key=name+':'+point.ref;if(user.placeWrites?.includes(key))throw Error('Это действие уже выполнялось. Проверь историю места перед повтором.');
    // Validate free text before saving a previously unsaved point.
    if(name==='add_place_note'&&(typeof args.text!=='string'||!args.text.trim()||args.text.length>4000||!['note','agreement'].includes(args.type||'note')))throw Error('Проверь текст и тип записи.');
    if(name==='attach_place_materials')for(const file of files){if(!file?.size||file.size>5*1024*1024)throw Error('Для карточки места нужны файлы до 5 МБ.');}
    user.placeWrites=[...(user.placeWrites||[]),key];await persist();guard();
    const place=await ensureSavedPlace(notebook,point);guard();point.savedId=place.id;
    if(name==='add_place_note'){await addPlaceNote(notebook,place.id,args.text,args.type||'note');guard();}
    else if(name==='attach_place_materials'){
      const visit=args.visit_id?(state.visitPlan?.rows||[]).find(p=>p.ref===args.visit_id):null;
      if(args.visit_id&&(!visit||visit.placeRef!==point.ref))throw Error('Выбери визит именно этого места.');
      const materials=new NotebookMaterials(notebook);
      for(const file of files){const id=await materials.attach(place.id,file,{caption:args.caption||'',visitId:visit?.visitId});guard();user.materialIds=[...(user.materialIds||[]),id];await persist();guard();}
    }else throw Error('Неизвестное действие с историей места.');
    const output=await read(point,false);return {saved:true,...output};
  }
  function quickRead(ids=[]){
    if(ids.length!==1||!state.places.some(p=>p.ref===ids[0]&&p.savedId))return null;
    const input=user.content.trim();
    if(/^(?:покажи|открой)\s+(?:мне\s+)?(?:историю|договор[её]нности|материалы)\s+(?:этого места|этого объекта|здесь|его|е[её])[.!?]*$/iu.test(input)||/^(?:show|open)\s+(?:the\s+)?(?:history|agreements|materials)(?:\s+(?:of|for))?\s+(?:this place|this location|it)[.!?]*$/iu.test(input))return {name:'get_place_history',args:{place_id:ids[0]}};
    return null;
  }
  return {run,quickRead};
}
