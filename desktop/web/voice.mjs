// Every choice represents an actual engine voice, never a renamed pitch alias.
const REMOTE_VOICES = new Set(['speechkit:jane','speechkit:omazh','speechkit:marina',
 'edge:ru-RU-SvetlanaNeural','edge:en-US-EmmaMultilingualNeural','edge:en-US-AvaMultilingualNeural']);
export const isRemoteVoice = uri => REMOTE_VOICES.has(uri);
const LANGUAGES=[['ru-RU','Русский'],['en-US','English'],['tr-TR','Türkçe'],['de-DE','Deutsch'],['fr-FR','Français'],['es-ES','Español']];
export function recognitionLanguage(saved, page, device) {
  const language=saved||page||device||'ru-RU';
  return LANGUAGES.find(([id])=>id.toLowerCase()===language.toLowerCase())?.[0]
    || LANGUAGES.find(([id])=>id.split('-')[0]===language.toLowerCase())?.[0] || language;
}
export function isVoiceEndCommand(text) {
  const phrase=String(text||'').toLowerCase().replace(/ё/g,'е').replace(/[^\p{L}\p{N}\s]/gu,' ').replace(/\s+/g,' ').trim()
    .replace(/^(?:велия|веля|velia)\s+/,'').replace(/^(?:пожалуйста|please)\s+/,'').replace(/\s+(?:пожалуйста|please)$/,'');
  return /^(?:заверши(?:ть)? (?:разговор|звонок)|законч(?:и|им) (?:разговор|звонок)|давай закончим(?: разговор)?|давай завершим(?: разговор)?|отключи(?:сь| разговор)|выключи голосовой (?:режим|разговор)|пока|спасибо пока|end (?:the )?(?:call|conversation)|hang up|goodbye|görüşmeyi bitir|aramayı bitir|auf wiedersehen|beende das gespräch|termine la conversation|au revoir|termina la conversación|adiós)$/.test(phrase);
}
export function availableVoices(voices, language) {
 const code=language.toLowerCase().split('-')[0], seen=new Set();
 return voices.filter(v=>[v.lang,...(v.languages||[])].some(lang=>lang?.toLowerCase().split('-')[0]===code) && v.voiceURI && !seen.has(v.voiceURI) && seen.add(v.voiceURI))
   .sort((a,b)=>Number(isRemoteVoice(a.voiceURI))-Number(isRemoteVoice(b.voiceURI))||Number(!!b.recommended)-Number(!!a.recommended)||Number(b.gender==='female')-Number(a.gender==='female')||Number(!!b.localService)-Number(!!a.localService)||a.name.localeCompare(b.name));
}
export function voiceChoice(voices, language, uri) {
 return voices.find(v=>v.voiceURI===uri)||availableVoices(voices,language)[0]||null;
}
export function speechReady(text, final=false) {
 if(final)return text.length;
 if(text.includes('```'))return 0;
 const match=/[.!?](?=\s|$)/.exec(text);
 if(match)return match.index+1;
 // Start at a complete clause/word rather than wait for a long sentence.
 const clause=/[,;:](?=\s)/g;let next;
 while((next=clause.exec(text)))if(next.index>=40)return next.index+1;
 if(text.length>=120){const cut=text.lastIndexOf(' ',120);if(cut>=40)return cut+1;}
 return 0;
}
// Keep spoken replies short enough for device speech engines without changing chat text.
export function speechChunks(text, limit = 240) {
  const clean = String(text || '').replace(/```[\s\S]*?(?:```|$)/g, ' Код сохранён в диалоге. ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' Изображение в диалоге. ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/https?:\/\/\S+/g, 'ссылка в диалоге')
    .replace(/[0-9#*]\uFE0F?\u20E3/g, '').replace(/[\p{Extended_Pictographic}\p{Regional_Indicator}\p{Emoji_Modifier}\u200D\uFE0F\u20E3]/gu,'')
    .replace(/^[ \t]*[>#*]+[ \t]*/gm, '').replace(/[*`_]/g, '').replace(/\s+/g, ' ').trim();
  const chunks = []; let rest = clean;
  while (rest.length > limit) {
    const slice = rest.slice(0, limit + 1);
    let cut = Math.max(slice.lastIndexOf('. '), slice.lastIndexOf('! '), slice.lastIndexOf('? '), slice.lastIndexOf('; '));
    cut = cut >= limit / 3 ? cut + 1 : slice.lastIndexOf(' ');
    if (cut < 1) cut = limit;
    chunks.push(rest.slice(0, cut).trim()); rest = rest.slice(cut).trim();
  }
  if (rest) chunks.push(rest);
  return chunks.filter(part=>/[\p{L}\p{N}]/u.test(part));
}

export function setupVoice({send, busy, toast, modelName = () => 'VELIA FLASH'}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let recognition = null, active = false, pending = false, epoch = 0, timer = null, speechEpoch = 0;
  let speechTimer = null, currentUtterance = null, deferredAnswer = null, waitingTimer = null, recognitionTimer=null, speechPaused = false, resumeWhenVisible=false;
  let catalogLoaded=false,catalogRequest=null,remoteVoices=[],remoteAudio=null,remoteAbort=null,remoteURL=null;
  const audioJobs=new Set();
  let language = recognitionLanguage('',document.documentElement.lang,navigator.language), voiceURI = '', rate = 1;
  try {language = recognitionLanguage(localStorage.getItem('velia-voice-language'),document.documentElement.lang,navigator.language); voiceURI = localStorage.getItem('velia-voice-uri') || ''; rate = Number(localStorage.getItem('velia-voice-rate')) || 1;} catch {}
  rate = Math.max(.7, Math.min(1.4, rate));
  const node = (tag, text, cls) => {const n = document.createElement(tag); if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog = node('dialog', '', 'voice-dialog'); dialog.setAttribute('aria-labelledby','voice-title');
  const title = node('h2', 'Разговор с Велией'); title.id='voice-title';
  const modelLabel = node('p', '', 'voice-model');
  const show = () => { modelLabel.textContent = modelName(); loadCatalog(); if(!dialog.open)dialog.showModal();if(!active)begin(); };
  const state = node('p', 'Подключаю микрофон…', 'voice-status'); state.setAttribute('role','status');
  const orb = node('div', 'V', 'voice-orb'); orb.setAttribute('aria-hidden','true');
  const close = node('button','×','voice-close');close.setAttribute('aria-label','Закрыть разговор');
  const reconnect=node('button','Повторить подключение','voice-reconnect');reconnect.hidden=true;reconnect.onclick=()=>begin();
  const resumeSpeech=node('button','Продолжить озвучку','voice-resume');resumeSpeech.hidden=true;
  resumeSpeech.onclick=()=>{if(!active||!speechPaused)return;speechPaused=false;resumeSpeech.hidden=true;speechEpoch++;pumpStream();};
  const voiceSelectors=new Set(), languageSelectors=new Set(), speechTests=new Set();
  const voiceHint=node('p','','voice-hint');
  const choices = () => availableVoices([...remoteVoices,...(synth?.getVoices()||[])],language);
  const effectiveURI = () => voiceURI || choices()[0]?.voiceURI || '';
  const canSpeak = () => !!synth || isRemoteVoice(effectiveURI());
  function refreshVoices(){
    const list=choices();
    if(voiceURI&&!list.some(v=>v.voiceURI===voiceURI)&&(!isRemoteVoice(voiceURI)||catalogLoaded))voiceURI='';
    for(const select of voiceSelectors){if(!select.isConnected&&select.dataset.mounted){voiceSelectors.delete(select);continue;}select.replaceChildren();const def=node('option','Автоматически'+(list[0]?' — '+list[0].name:''));def.value='';select.append(def);for(const v of list){const o=node('option',v.name+' · '+(v.languages?'Мультиязычный':v.lang)+(isRemoteVoice(v.voiceURI)?' · Онлайн':'')+(v.gender==='female'?' · Женский':''));o.value=v.voiceURI;select.append(o);}select.value=voiceURI;select.dataset.mounted='true';}
    for(const b of speechTests) b.disabled=!canSpeak();
    voiceHint.textContent=remoteVoices.length?'Онлайн-голоса дают одинаковый тембр на разных устройствах. Голос телефона обычно запускается быстрее.':list.length===1?'Для выбранного языка доступен один голос.':list.length===0?'Используется голос браузера по умолчанию.':'';
    voiceHint.hidden=!voiceHint.textContent;
  }
  async function loadCatalog(){if(catalogRequest)return catalogRequest;catalogRequest=(async()=>{try{const response=await fetch('/web-api/v1/voice/voices',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)return;const data=await response.json();catalogLoaded=true;remoteVoices=Array.isArray(data.voices)?data.voices.filter(v=>isRemoteVoice(v.voiceURI)):[];refreshVoices();}catch{}finally{catalogRequest=null;}})();return catalogRequest;}
  function releasePlayback(){if(remoteAudio){remoteAudio.pause();remoteAudio.onended=null;remoteAudio.onerror=null;remoteAudio=null;}if(remoteURL){URL.revokeObjectURL(remoteURL);remoteURL=null;}}
  function cancelAudio(){for(const job of audioJobs)job.controller.abort();audioJobs.clear();remoteAbort?.abort();remoteAbort=null;releasePlayback();}
  function downloadPart(part,uri){
    const existing=[...audioJobs].find(job=>job.part===part&&job.uri===uri&&job.rate===rate&&job.epoch===speechEpoch);
    if(existing)return existing;
    const job={part,uri,rate,epoch:speechEpoch,controller:new AbortController()};audioJobs.add(job);
    job.ready=(async()=>{try{const response=await fetch('/web-api/v1/voice/speech',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Velia-Request':'1'},body:JSON.stringify({text:part,voice:uri,rate:job.rate}),signal:job.controller.signal});if(!response.ok)throw Error('tts_unavailable');const blob=await response.blob();if(blob.size>2*1024*1024)throw Error('tts_unavailable');return blob;}catch{return null;}})();
    return job;
  }
  function primeNext(){
    const uri=effectiveURI();
    if(active&&!speechPaused&&!streamBlocked&&!document.hidden&&currentUtterance&&streamQueue.length&&isRemoteVoice(uri)&&audioJobs.size<2)downloadPart(streamQueue[0],uri);
  }
  async function playPart(part,done,fail){
    const uri=effectiveURI();
    if(!isRemoteVoice(uri)){if(!synth){fail();return;}const u=new SpeechSynthesisUtterance(part);u.lang=language;u.rate=rate;u.pitch=1;u.volume=1;u.voice=voiceChoice(synth.getVoices(),language,uri);u.onend=done;u.onerror=fail;currentUtterance=u;try{synth.speak(u);}catch{fail();}return;}
    const token=speechEpoch,job=downloadPart(part,uri);remoteAbort=job.controller;currentUtterance={};
    try{const blob=await job.ready;audioJobs.delete(job);if(token!==speechEpoch)return;if(!blob)throw Error('tts_unavailable');remoteAbort=null;remoteURL=URL.createObjectURL(blob);remoteAudio=new Audio(remoteURL);remoteAudio.onended=()=>{releasePlayback();done();};remoteAudio.onerror=()=>{releasePlayback();fail();};await remoteAudio.play();}catch(error){if(token===speechEpoch&&error.name!=='AbortError'){cancelAudio();fail();}}
  }
  function voiceControl(){const label=node('label','Голос Велии','voice-profile'), select=node('select');select.setAttribute('aria-label','Голос');voiceSelectors.add(select);select.onchange=()=>{const reopen=dialog.open;stop();voiceURI=select.value;save();refreshVoices();if(reopen)begin();};label.append(select);return label;}
  function languageControl(){
    const label=node('label','Язык разговора','voice-profile'),select=node('select');select.setAttribute('aria-label','Язык разговора');
    for(const [id,name] of LANGUAGES){const option=node('option',name);option.value=id;select.append(option);}
    if(!LANGUAGES.some(([id])=>id===language)){const option=node('option',language);option.value=language;select.append(option);}
    select.value=language;languageSelectors.add(select);select.onchange=()=>{const reopen=dialog.open;stop();language=select.value;save();refreshVoices();for(const other of languageSelectors)other.value=language;if(reopen)begin();};label.append(select);return label;
  }
  dialog.append(close,title,modelLabel,orb,state,voiceControl(),languageControl(),resumeSpeech,reconnect,node('p','Ответы сохраняются в диалоге.','voice-hint'));
  document.body.append(dialog);refreshVoices();synth?.addEventListener('voiceschanged',refreshVoices);
  const status = (text, phase='idle') => {state.textContent=text;dialog.dataset.phase=phase;reconnect.hidden=active||phase!=='error';};
  const save=()=>{try {localStorage.setItem('velia-voice-language',language);localStorage.setItem('velia-voice-uri',voiceURI);localStorage.setItem('velia-voice-rate',String(rate));}catch{}};
  let streamText='',streamCursor=0,streamQueue=[],streamDone=false,streamBlocked=false;
  function stop(){resumeWhenVisible=false;speechPaused=false;resumeSpeech.hidden=true;cancelAudio();streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;clearTimeout(waitingTimer);clearTimeout(recognitionTimer);deferredAnswer=null;active=false;pending=false;epoch++;speechEpoch++;clearTimeout(timer);clearTimeout(speechTimer);currentUtterance=null;const old=recognition;recognition=null;old?.abort();synth?.cancel();status('Разговор завершён.');}
  close.onclick=()=>{stop();dialog.close();};dialog.addEventListener('cancel',stop);dialog.addEventListener('close',stop);
  function later(token, delay=400){clearTimeout(timer);timer=setTimeout(()=>{if(active&&token===epoch)listen();},delay);}
  function listen(){
    if(!active||pending||!Recognition||document.hidden)return;
    if(busy()){later(epoch);return;}
    const token=epoch, r=new Recognition();recognition=r;r.lang=language;r.interimResults=true;r.continuous=true;r.maxAlternatives=1;
    let final='', interim='', failed=false;
    status('Слушаю тебя…','listening');
    const valid=()=>active&&token===epoch&&recognition===r;
    r.onresult=e=>{if(!valid())return;clearTimeout(recognitionTimer);final='';interim='';for(const result of Array.from(e.results)){if(result.isFinal)final+=result[0].transcript+' ';else interim+=result[0].transcript+' ';}if(final.trim()&&!interim.trim())recognitionTimer=setTimeout(finishTurn,450);};
    r.onerror=e=>{if(!valid())return;if(e.error==='no-speech')return;failed=true;clearTimeout(recognitionTimer);active=false;recognition=null;r.abort();status(({ 'not-allowed':'Разреши микрофон в настройках сайта и повтори подключение.', 'service-not-allowed':'Браузер запретил распознавание речи.', 'audio-capture':'Микрофон не найден или занят другим приложением.', network:'Сервис распознавания недоступен. Проверь соединение и повтори подключение.', 'language-not-supported':'Выбери другой язык разговора.' })[e.error]||'Не удалось распознать речь. Повтори подключение.','error');};
    async function finishTurn(){if(!valid())return;recognition=null;clearTimeout(recognitionTimer);r.stop?.();if(failed)return;const text=final.trim();if(interim.trim()){status('Не расслышала конец фразы. Повтори, пожалуйста.','listening');later(token,800);return;}if(!text){status('Жду твою реплику…','listening');later(token,800);return;}if(isVoiceEndCommand(text)){close.click();return;}pending=true;streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;status('Велия готовит ответ…','thinking');waitingTimer=setTimeout(()=>{if(pending&&active&&!speechPaused)status('Ответ задерживается. Запрос ещё выполняется…','thinking');},20000);try{await send(text);if(pending&&deferredAnswer===null&&active&&token===epoch)failure();}catch{if(token===epoch)failure();}}
    r.onend=finishTurn;
    try{r.start();}catch{recognition=null;active=false;status('Микрофон недоступен. Проверь разрешение и повтори.','error');}
  }
  function begin(){if(active)return;stop();if(!Recognition){status('Распознавание речи недоступно. Открой разговор в Chrome или Edge.','error');return;}if(synth){const unlock=new SpeechSynthesisUtterance('.');unlock.volume=0;unlock.lang=language;synth.speak(unlock);synth.resume?.();}active=true;if(busy())status('Дождусь завершения ответа…','thinking');listen();}
  function speak(text, resume=false){
    if(!resume)stop();
    if(!canSpeak()){if(resume){active=false;status('Озвучивание недоступно. Ответ сохранён в диалоге.','error');}else toast('Озвучивание недоступно в этом браузере.');return;}
    const token=++speechEpoch, conversation=epoch, chunks=speechChunks(text);let index=0;synth?.cancel();
    const next=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);currentUtterance=null;if(index>=chunks.length){if(resume&&active&&conversation===epoch){status('Жду твою реплику…','listening');later(conversation);}return;}
      const part=chunks[index++];
      if(resume)status('Велия говорит…','speaking');
      const fail=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);speechEpoch++;cancelAudio();currentUtterance=null;active=false;synth?.cancel();status('Озвучивание прервалось. Полный ответ сохранён в диалоге.','error');};
      speechTimer=setTimeout(fail,45000);playPart(part,next,fail);
    };next();
  }
  function pumpStream(){
    if(!active||speechPaused||streamBlocked||document.hidden)return;
    if(currentUtterance){primeNext();return;}
    if(!streamQueue.length){if(streamDone){status('Жду твою реплику…','listening');later(epoch);}else status('Велия готовит продолжение…','thinking');return;}
    const part=streamQueue.shift(),token=speechEpoch;
    status('Велия говорит…','speaking');
    const done=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);currentUtterance=null;pumpStream();};
    const fail=()=>{
      if(token!==speechEpoch)return;
      // Keep the failed phrase and incoming answer tail. A user gesture can
      // resume the same voice without another model request or replaying the start.
      speechEpoch++;speechPaused=true;streamQueue.unshift(part);cancelAudio();
      clearTimeout(speechTimer);clearTimeout(waitingTimer);clearTimeout(timer);currentUtterance=null;synth?.cancel();
      resumeSpeech.hidden=false;status('Озвучка остановилась. Нажми «Продолжить озвучку».','error');
    };
    speechTimer=setTimeout(fail,45000);playPart(part,done,fail);
    primeNext();
  }
  function update(text,final=false){
    if(!active||(!pending&&!final)||streamBlocked)return;
    text=String(text||'');
    if(streamCursor&&text.slice(0,streamCursor)!==streamText.slice(0,streamCursor)){
      streamBlocked=true;speechPaused=false;resumeSpeech.hidden=true;streamQueue=[];speechEpoch++;cancelAudio();clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();return;
    }
    streamText=text;if(document.hidden)return;
    let cut;while((cut=speechReady(streamText.slice(streamCursor),final))>0){
      const raw=streamText.slice(streamCursor,streamCursor+cut);streamCursor+=cut;
      streamQueue.push(...speechChunks(raw));
    }
    if(final)streamDone=true;
    if(streamQueue.length)clearTimeout(waitingTimer);
    pumpStream();
  }
  function complete(text){
    clearTimeout(waitingTimer);if(!pending||!active)return;
    if(document.hidden){deferredAnswer=text;status('Ответ готов','thinking');return;}
    if(streamBlocked){pending=false;active=false;status('Ответ уточнён. Полный текст сохранён в диалоге.','error');return;}
    if(!speechChunks(text).length){if(!String(text||'').trim())failure(true);else {streamDone=true;pending=false;pumpStream();}return;}
    update(text,true);pending=false;
  }
  function failure(force=false,detail=''){clearTimeout(waitingTimer);if(pending||force){pending=false;deferredAnswer=null;speechPaused=false;resumeSpeech.hidden=true;active=false;speechEpoch++;cancelAudio();clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();status(typeof detail==='string'&&detail.trim()?detail.slice(0,500):'Ответ не получен. Проверь диалог и повтори подключение.','error');}}
  function settings(parent){
    loadCatalog();
    parent.append(node('p','Распознавание работает через браузер. Для озвучивания доступны голоса устройства и подключённые онлайн-голоса; онлайн-озвучивание передаёт текст ответа сервису речи.','feature-notice'));
    const panel=node('div','','feature-form');parent.append(panel);panel.append(voiceControl());refreshVoices();
    panel.append(languageControl());
    const speed=node('label','Скорость речи'), select=node('select');select.setAttribute('aria-label','Скорость речи');for(const [value,name] of [[.8,'Медленнее'],[1,'Обычная'],[1.2,'Быстрее']]){const o=node('option',name);o.value=value;select.append(o);}select.value=String(rate);select.onchange=()=>{const reopen=dialog.open;stop();rate=Number(select.value);save();if(reopen)begin();};speed.append(select);panel.append(speed);
    const test=node('button','Послушать голос');speechTests.add(test);test.disabled=!canSpeak();test.onclick=()=>speak('Привет! Я Велия. Давай обсудим твою идею.');panel.append(test);
    const open=node('button','Открыть голосовой разговор','feature-primary');open.onclick=show;open.disabled=!Recognition;panel.append(open);
    if(!Recognition)panel.append(node('p','Этот браузер не поддерживает распознавание. Озвучивание ответов доступно отдельно.','feature-notice'));
  }
  document.addEventListener('visibilitychange',()=>{
    if(!document.hidden&&resumeWhenVisible&&dialog.open){resumeWhenVisible=false;begin();return;}
    if(!active)return;
    if(document.hidden){if(pending){synth?.pause?.();remoteAudio?.pause();return;}stop();resumeWhenVisible=dialog.open;status('Разговор приостановлен: страница была свёрнута.');}
    else {synth?.resume?.();remoteAudio?.play().catch(()=>failure(true));if(deferredAnswer!==null){const answer=deferredAnswer;deferredAnswer=null;complete(answer);}else if(pending)update(streamText);pumpStream();}
  });
  return {settings,update,complete,failure,speak,open:show,stop};
}
