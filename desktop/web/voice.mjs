import {canStreamAudio,attachAudioStream,trackPlaybackStatus} from './speech-audio.mjs';
// Every choice represents an actual engine voice, never a renamed pitch alias.
const REMOTE_VOICES = new Set(['speechkit:jane','speechkit:omazh','speechkit:marina',
 'edge:ru-RU-SvetlanaNeural','edge:en-US-EmmaMultilingualNeural','edge:en-US-AvaMultilingualNeural']);
export const isRemoteVoice = uri => REMOTE_VOICES.has(uri);
const LANGUAGES=[['ru-RU','Русский'],['en-US','English'],['tr-TR','Türkçe'],['de-DE','Deutsch'],['fr-FR','Français'],['es-ES','Español']];
// ISO 639-1 language choices plus every locale advertised by the device/voices.
// An entry is a selection, not a promise that the browser can recognize it.
const LANGUAGE_CODES='aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu'.split(' ');
function canonicalLanguage(value) {
 try {return Intl.getCanonicalLocales(String(value||'').trim().replace(/_/g,'-'))[0]||'';}catch{return '';}
}
export function conversationLanguages(current='',devices=[],voices=[]) {
 const choices=new Map(LANGUAGES), names=typeof Intl.DisplayNames==='function'?new Intl.DisplayNames(['ru'],{type:'language'}):null;
 for(const tag of [...LANGUAGE_CODES,...devices,current,...voices.flatMap(v=>[v.lang,...(v.languages||[])])]){
  const id=canonicalLanguage(tag);if(id&&!choices.has(id))choices.set(id,names?.of(id)||id);
 }
 return [...choices].sort((a,b)=>a[1].localeCompare(b[1],'ru'));
}
export function recognitionLanguage(saved, page, device, account='') {
  const language=canonicalLanguage(account)||canonicalLanguage(saved)||canonicalLanguage(device)||canonicalLanguage(page)||'ru-RU';
  return LANGUAGES.find(([id])=>id.toLowerCase()===language.toLowerCase())?.[0]
    || LANGUAGES.find(([id])=>id.split('-')[0]===language.toLowerCase())?.[0] || language;
}
// Keep accepting final results after stop(): the browser may finalize captured
// audio asynchronously. Never submit an interim hypothesis or a truncated turn.
export function bindRecognitionTurn(r,{valid,finish,incomplete,setTimer=setTimeout,clearTimer=clearTimeout}) {
  let final='',interim='',idleTimer=null,endTimer=null,stopping=false,closed=false,speaking=false;
  const alive=()=>!closed&&valid();
  function cancel(){closed=true;clearTimer(idleTimer);clearTimer(endTimer);}
  function scheduleStop(){
    clearTimer(idleTimer);
    if(!alive()||stopping||speaking||!final.trim()||interim.trim())return;
    idleTimer=setTimer(()=>{
      if(!alive())return;
      stopping=true;
      endTimer=setTimer(()=>{if(!alive())return;cancel();incomplete();r.abort?.();},2000);
      try{r.stop();}catch{cancel();incomplete();r.abort?.();}
    },1000);
  }
  r.onresult=e=>{
    if(!alive())return;
    clearTimer(idleTimer);final='';interim='';
    // One-shot engines can still return several independent result slots.
    // Keep them all; collapse only a demonstrably cumulative revision.
    const results=Array.from(e.results);
    for(const [index,result] of results.entries()){
      const text=String(result[0]?.transcript||'').trim();
      if(!text)continue;
      if(result.isFinal){
        const previous=final.trim();
        const expandedLater=text===previous&&results.slice(index+1).some(next=>next.isFinal&&String(next[0]?.transcript||'').trim().startsWith(previous+' '));
        if(r.continuous===false&&previous&&(expandedLater||text.startsWith(previous+' ')))final=text+' ';
        else final+=text+' ';
      }else interim+=text+' ';
    }
    scheduleStop();
  };
  r.onspeechstart=()=>{if(alive()){speaking=true;clearTimer(idleTimer);}};
  r.onspeechend=()=>{if(alive()){speaking=false;scheduleStop();}};
  r.onend=()=>{if(!alive())return;cancel();if(interim.trim())incomplete();else finish(final.trim());};
  return cancel;
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
export function fastDeviceVoice(voices,language) {
 return availableVoices(voices,language).find(v=>!isRemoteVoice(v.voiceURI)&&v.localService===true)||null;
}
export function automaticVoice(voices, language) {
 return fastDeviceVoice(voices,language)||availableVoices(voices,language)[0]||null;
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

export function setupVoice({send, busy, toast, modelName = () => 'VELIA FLASH', accountLanguage = () => ''}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let recognition = null, active = false, pending = false, epoch = 0, timer = null, speechEpoch = 0, recognitionFailures = 0;
  let speechTimer = null, currentUtterance = null, deferredAnswer = null, waitingTimer = null, cancelRecognitionTurn=()=>{}, speechPaused = false, resumeWhenVisible=false;
  let catalogLoaded=false,catalogRequest=null,remoteVoices=[],remoteAudio=null,remoteAbort=null,remoteURL=null,remoteStream=null,releasePlaybackStatus=null;
  const audioJobs=new Set();
  let language = recognitionLanguage('',document.documentElement.lang,navigator.language,accountLanguage()), voiceURI = '', rate = 1;
  try {voiceURI = localStorage.getItem('velia-voice-mode')==='manual'?(localStorage.getItem('velia-voice-uri')||''):''; rate = Number(localStorage.getItem('velia-voice-rate')) || 1;} catch {}
  rate = Math.max(.7, Math.min(1.4, rate));
  const node = (tag, text, cls) => {const n = document.createElement(tag); if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog = node('dialog', '', 'voice-dialog'); dialog.setAttribute('aria-labelledby','voice-title');
  const title = node('h2', 'Разговор с Велией'); title.id='voice-title';
  const modelLabel = node('p', '', 'voice-model');
  const show = () => { syncLanguage(); modelLabel.textContent = modelName(); loadCatalog(); if(!dialog.open)dialog.showModal();if(!active)begin(); };
  const state = node('p', 'Подключаю микрофон…', 'voice-status'); state.setAttribute('role','status');
  const orb = node('div', 'V', 'voice-orb'); orb.setAttribute('aria-hidden','true');
  const close = node('button','×','voice-close');close.setAttribute('aria-label','Закрыть разговор');
  const reconnect=node('button','Повторить подключение','voice-reconnect');reconnect.hidden=true;reconnect.onclick=()=>begin();
  const resumeSpeech=node('button','Продолжить озвучку','voice-resume');resumeSpeech.hidden=true;
  resumeSpeech.onclick=()=>{if(!active||!speechPaused)return;speechPaused=false;resumeSpeech.hidden=true;speechEpoch++;pumpStream();};
  const voiceSelectors=new Set(), speechTests=new Set();
  const fastVoice=node('button','Быстрый голос телефона','voice-fast');fastVoice.hidden=true;
  fastVoice.onclick=()=>{const selected=fastDeviceVoice(synth?.getVoices()||[],language);if(!selected)return;const reopen=dialog.open;stop();voiceURI='';save();refreshVoices();if(reopen)begin();};
  const voiceHint=node('p','','voice-hint');
  const choices = () => availableVoices([...remoteVoices,...(synth?.getVoices()||[])],language);
  const effectiveURI = () => voiceURI || automaticVoice([...remoteVoices,...(synth?.getVoices()||[])],language)?.voiceURI || '';
  function syncLanguage(){const next=recognitionLanguage('',document.documentElement.lang,navigator.language,accountLanguage());if(next!==language){const reopen=dialog.open;stop();language=next;refreshVoices();if(reopen)begin();}else refreshVoices();}
  const canSpeak = () => !!synth || isRemoteVoice(effectiveURI());
  function refreshVoices(){
    const list=choices();
    if(voiceURI&&!list.some(v=>v.voiceURI===voiceURI)&&(!isRemoteVoice(voiceURI)||catalogLoaded))voiceURI='';
    for(const select of voiceSelectors){if(!select.isConnected&&select.dataset.mounted){voiceSelectors.delete(select);continue;}select.replaceChildren();const def=node('option','Автоматически'+(automaticVoice(list,language)?' — '+automaticVoice(list,language).name:''));def.value='';select.append(def);for(const v of list){const o=node('option',v.name+' · '+(v.languages?'Мультиязычный':v.lang)+(isRemoteVoice(v.voiceURI)?' · Онлайн':'')+(v.gender==='female'?' · Женский':''));o.value=v.voiceURI;select.append(o);}select.value=voiceURI;select.dataset.mounted='true';}
    for(const b of speechTests) b.disabled=!canSpeak();
    const deviceVoice=fastDeviceVoice(synth?.getVoices()||[],language);
    const selectedVoice=list.find(v=>v.voiceURI===effectiveURI());
    fastVoice.hidden=!deviceVoice||selectedVoice?.localService===true&&!isRemoteVoice(selectedVoice.voiceURI);
    fastVoice.textContent=deviceVoice?'Быстрый голос телефона · '+deviceVoice.name:'Быстрый голос телефона';
    fastVoice.title=deviceVoice?.name||'';
    voiceHint.textContent=list.length===0?'Для этого языка браузер не сообщил подходящий голос. Распознавание и озвучка зависят от устройства.':remoteVoices.length?'Онлайн-голоса дают одинаковый тембр на разных устройствах. Голос телефона обычно запускается быстрее.':list.length===1?'Для выбранного языка доступен один голос.':'';
    voiceHint.hidden=!voiceHint.textContent;
  }
  async function loadCatalog(){if(catalogRequest)return catalogRequest;catalogRequest=(async()=>{try{const response=await fetch('/web-api/v1/voice/voices',{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)return;const data=await response.json();catalogLoaded=true;remoteVoices=Array.isArray(data.voices)?data.voices.filter(v=>isRemoteVoice(v.voiceURI)):[];refreshVoices();}catch{}finally{catalogRequest=null;}})();return catalogRequest;}
  function releasePlayback(){releasePlaybackStatus?.();releasePlaybackStatus=null;remoteStream?.cancel();remoteStream=null;if(remoteAudio){remoteAudio.onended=null;remoteAudio.onerror=null;remoteAudio.pause();remoteAudio=null;}if(remoteURL){URL.revokeObjectURL(remoteURL);remoteURL=null;}}
  function cancelAudio(){for(const job of audioJobs)job.controller.abort();audioJobs.clear();remoteAbort?.abort();remoteAbort=null;releasePlayback();}
  function downloadPart(part,uri){
    const existing=[...audioJobs].find(job=>job.part===part&&job.uri===uri&&job.rate===rate&&job.epoch===speechEpoch);
    if(existing)return existing;
    const job={part,uri,rate,epoch:speechEpoch,controller:new AbortController()};audioJobs.add(job);
    job.ready=(async()=>{try{const response=await fetch('/web-api/v1/voice/speech'+(canStreamAudio()?'?stream=1':''),{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Velia-Request':'1','X-Velia-Voice-Client':'playback-clock-v1'},body:JSON.stringify({text:part,voice:uri,rate:job.rate}),signal:job.controller.signal});if(!response.ok)throw Error('tts_unavailable');if(canStreamAudio()&&response.headers?.get('X-Velia-Audio-Stream')==='1'&&response.body?.getReader)return {response};const blob=await response.blob();if(blob.size>2*1024*1024)throw Error('tts_unavailable');return {blob};}catch{return null;}})();
    return job;
  }
  function primeNext(){
    const uri=effectiveURI();
    if(active&&!speechPaused&&!streamBlocked&&!document.hidden&&currentUtterance&&streamQueue.length&&isRemoteVoice(uri)&&audioJobs.size<2)downloadPart(streamQueue[0],uri);
  }
  async function playPart(part,done,fail){
    const uri=effectiveURI(),token=speechEpoch;
    const speaking=()=>{if(token===speechEpoch&&active&&!document.hidden&&!speechPaused)status('Велия говорит…','speaking');};
    const buffering=()=>{if(token===speechEpoch&&active&&!document.hidden&&!speechPaused)status('Готовлю голос…','thinking');};
    if(!isRemoteVoice(uri)){if(!synth){fail();return;}const u=new SpeechSynthesisUtterance(part);u.lang=language;u.rate=rate;u.pitch=1;u.volume=1;u.voice=voiceChoice(synth.getVoices(),language,uri);u.onstart=speaking;u.onend=done;u.onerror=fail;currentUtterance=u;try{synth.speak(u);}catch{fail();}return;}
    const job=downloadPart(part,uri);remoteAbort=job.controller;currentUtterance={};
    try{const audio=await job.ready;audioJobs.delete(job);if(token!==speechEpoch)return;if(!audio)throw Error('tts_unavailable');remoteAudio=new Audio();
      if(audio.response){remoteStream=attachAudioStream(audio.response,{signal:job.controller.signal,onFailure:()=>{if(token===speechEpoch){releasePlayback();fail();}}});remoteURL=remoteStream.url;}
      else remoteURL=URL.createObjectURL(audio.blob);
      const player=remoteAudio,valid=()=>token===speechEpoch&&remoteAudio===player;
      releasePlaybackStatus=trackPlaybackStatus(player,{valid,speaking,buffering});
      player.onended=()=>{if(valid()){releasePlayback();done();}};player.onerror=()=>{if(valid()){releasePlayback();fail();}};player.src=remoteURL;await player.play();
    }catch(error){if(token===speechEpoch&&error.name!=='AbortError'){cancelAudio();fail();}}
  }
  function voiceControl(){const label=node('label','Голос Велии','voice-profile'), select=node('select');select.setAttribute('aria-label','Голос');voiceSelectors.add(select);select.onchange=()=>{const reopen=dialog.open;stop();voiceURI=select.value;save();refreshVoices();if(reopen)begin();};label.append(select);return label;}
  dialog.append(close,title,modelLabel,orb,state,voiceControl(),fastVoice,resumeSpeech,reconnect,node('p','Ответы сохраняются в диалоге.','voice-hint'));
  document.body.append(dialog);refreshVoices();synth?.addEventListener('voiceschanged',refreshVoices);
  const status = (text, phase='idle') => {state.textContent=text;dialog.dataset.phase=phase;reconnect.hidden=active||phase!=='error';};
  const save=()=>{try {localStorage.setItem('velia-voice-mode',voiceURI?'manual':'auto');localStorage.setItem('velia-voice-uri',voiceURI);localStorage.setItem('velia-voice-rate',String(rate));}catch{}};
  let streamText='',streamCursor=0,streamQueue=[],streamDone=false,streamBlocked=false;
  function stop(){resumeWhenVisible=false;speechPaused=false;resumeSpeech.hidden=true;recognitionFailures=0;cancelAudio();streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;clearTimeout(waitingTimer);cancelRecognitionTurn();deferredAnswer=null;active=false;pending=false;epoch++;speechEpoch++;clearTimeout(timer);clearTimeout(speechTimer);currentUtterance=null;const old=recognition;recognition=null;old?.abort();synth?.cancel();status('Разговор завершён.');}
  close.onclick=()=>{stop();dialog.close();};dialog.addEventListener('cancel',stop);dialog.addEventListener('close',stop);
  function later(token, delay=400){clearTimeout(timer);timer=setTimeout(()=>{if(active&&token===epoch)listen();},delay);}
  function listen(){
    if(!active||pending||recognition||currentUtterance||speechPaused||!Recognition||document.hidden)return;
    clearTimeout(timer);
    if(busy()){later(epoch);return;}
    const token=epoch, r=new Recognition();recognition=r;r.lang=language;r.interimResults=true;r.continuous=false;r.maxAlternatives=1;
    status('Слушаю тебя…','listening');
    const valid=()=>active&&token===epoch&&recognition===r;
    const incomplete=()=>{if(!valid())return;recognition=null;status('Не расслышала конец фразы. Повтори, пожалуйста.','listening');later(token,800);};
    cancelRecognitionTurn=bindRecognitionTurn(r,{valid,finish:finishTurn,incomplete});
    r.onerror=e=>{
      if(!valid()||e.error==='no-speech')return;
      cancelRecognitionTurn();recognition=null;r.abort();
      const recoverable=['network','aborted'].includes(e.error), attempt=++recognitionFailures;
      // Report only a bounded error code; never send speech or audio diagnostics.
      const code=['network','aborted','not-allowed','service-not-allowed','audio-capture','language-not-supported'].includes(e.error)?e.error:'unknown';
      fetch('/web-api/v1/voice/events',{method:'POST',credentials:'same-origin',keepalive:true,
        headers:{'Content-Type':'application/json','X-Velia-Request':'1'},
        body:JSON.stringify({event:'recognition_error',code,attempt:Math.min(attempt,3)})}).catch(()=>{});
      if(recoverable&&attempt<=2){status('Восстанавливаю распознавание. Повтори фразу после подключения…','thinking');later(token,attempt*750);return;}
      active=false;status(({ 'not-allowed':'Разреши микрофон в настройках сайта и повтори подключение.', 'service-not-allowed':'Браузер запретил распознавание речи.', 'audio-capture':'Микрофон не найден или занят другим приложением.', network:'Сервис распознавания недоступен. Проверь соединение и повтори подключение.', 'language-not-supported':'Браузер не поддерживает распознавание языка аккаунта. Напиши сообщение текстом.' })[e.error]||'Не удалось распознать речь. Повтори подключение.','error');
    };
    async function finishTurn(text){if(!valid())return;recognition=null;if(!text){status('Жду твою реплику…','listening');later(token,800);return;}recognitionFailures=0;if(isVoiceEndCommand(text)){close.click();return;}pending=true;streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;status('Велия готовит ответ…','thinking');waitingTimer=setTimeout(()=>{if(pending&&active&&!speechPaused)status('Ответ задерживается. Запрос ещё выполняется…','thinking');},20000);try{await send(text);if(pending&&deferredAnswer===null&&active&&token===epoch)failure();}catch{if(token===epoch)failure();}}
    try{r.start();}catch{cancelRecognitionTurn();recognition=null;active=false;status('Микрофон недоступен. Проверь разрешение и повтори.','error');}
  }
  function begin(){if(active)return;stop();if(!Recognition){status('Распознавание речи недоступно. Открой разговор в Chrome или Edge.','error');return;}if(synth){const unlock=new SpeechSynthesisUtterance('.');unlock.volume=0;unlock.lang=language;synth.speak(unlock);synth.resume?.();}active=true;if(busy())status('Дождусь завершения ответа…','thinking');listen();}
  function speak(text, resume=false){
    if(!resume)stop();
    if(!canSpeak()){if(resume){active=false;status('Озвучивание недоступно. Ответ сохранён в диалоге.','error');}else toast('Озвучивание недоступно в этом браузере.');return;}
    const token=++speechEpoch, conversation=epoch, chunks=speechChunks(text);let index=0;synth?.cancel();
    const next=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);currentUtterance=null;if(index>=chunks.length){if(resume&&active&&conversation===epoch){status('Жду твою реплику…','listening');later(conversation);}return;}
      const part=chunks[index++];
      if(resume)status('Готовлю голос…','thinking');
      const fail=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);speechEpoch++;cancelAudio();currentUtterance=null;active=false;synth?.cancel();status('Озвучивание прервалось. Полный ответ сохранён в диалоге.','error');};
      speechTimer=setTimeout(fail,45000);playPart(part,next,fail);
    };next();
  }
  function pumpStream(){
    if(!active||speechPaused||streamBlocked||document.hidden)return;
    if(currentUtterance){primeNext();return;}
    if(!streamQueue.length){if(streamDone){status('Жду твою реплику…','listening');later(epoch);}else status('Велия готовит продолжение…','thinking');return;}
    const part=streamQueue.shift(),token=speechEpoch;
    status('Готовлю голос…','thinking');
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
      streamBlocked=true;speechPaused=false;resumeSpeech.hidden=true;streamQueue=[];speechEpoch++;cancelAudio();clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();status('Уточняю ответ…','thinking');return;
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
    if(streamBlocked||streamCursor&&String(text||'').slice(0,streamCursor)!==streamText.slice(0,streamCursor)){
      // A real correction supersedes draft audio, but must not terminate the
      // conversation. Read the final answer once, then reopen the microphone.
      streamBlocked=false;speechPaused=false;resumeSpeech.hidden=true;streamQueue=[];
      streamText='';streamCursor=0;streamDone=false;speechEpoch++;cancelAudio();
      clearTimeout(speechTimer);clearTimeout(timer);currentUtterance=null;synth?.cancel();
    }
    if(!speechChunks(text).length){if(!String(text||'').trim())failure(true);else {streamDone=true;pending=false;pumpStream();}return;}
    update(text,true);pending=false;
  }
  function backgroundAccepted(text=''){
    modelLabel.textContent=modelName();
    if(text){complete(text);return;}
    clearTimeout(waitingTimer);pending=false;streamDone=true;if(active)pumpStream();
  }
  function failure(force=false,detail=''){clearTimeout(waitingTimer);if(pending||force){pending=false;deferredAnswer=null;speechPaused=false;resumeSpeech.hidden=true;active=false;speechEpoch++;cancelAudio();clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();status(typeof detail==='string'&&detail.trim()?detail.slice(0,500):'Ответ не получен. Проверь диалог и повтори подключение.','error');}}
  function settings(parent){
    syncLanguage();loadCatalog();
    parent.append(node('p','Распознавание работает через браузер. Для озвучивания доступны голоса устройства и подключённые онлайн-голоса; онлайн-озвучивание передаёт текст ответа сервису речи.','feature-notice'));
    parent.append(node('p','В режиме «Автоматически» язык берётся из аккаунта, а локальный голос устройства выбирается первым для меньшей задержки. Если его нет, используется доступный голос для этого языка. Голос можно выбрать вручную; тембр зависит от устройства. Чтобы получить ответ на другом языке, попроси об этом в разговоре.','feature-notice'));
    const panel=node('div','','feature-form');parent.append(panel);panel.append(voiceControl());refreshVoices();
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
  return {settings,update,complete,failure,speak,open:show,stop,syncLanguage,backgroundAccepted};
}
