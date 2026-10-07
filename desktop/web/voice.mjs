// Device voices must represent actual engine voices, not renamed aliases.
export function availableVoices(voices, language) {
 const code=language.toLowerCase().split('-')[0], seen=new Set();
 return voices.filter(v=>v.lang.toLowerCase().split('-')[0]===code && v.voiceURI && !seen.has(v.voiceURI) && seen.add(v.voiceURI))
   .sort((a,b)=>Number(b.localService)-Number(a.localService)||a.name.localeCompare(b.name));
}
export function voiceChoice(voices, language, uri) {
 return voices.find(v=>v.voiceURI===uri)||availableVoices(voices,language)[0]||null;
}
export function speechReady(text, final=false) {
 if(final)return text.length;
 if(text.includes('```'))return 0;
 const match=/[.!?](?=\s|$)/.exec(text);
 return match?match.index+1:0;
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
  return chunks;
}

export function setupVoice({send, busy, toast}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;
  let recognition = null, active = false, pending = false, epoch = 0, timer = null, speechEpoch = 0;
  let speechTimer = null, currentUtterance = null, deferredAnswer = null, waitingTimer = null;
  let language = navigator.language || 'ru-RU', voiceURI = '', rate = 1;
  try {language = localStorage.getItem('velia-voice-language') || language; voiceURI = localStorage.getItem('velia-voice-uri') || ''; rate = Number(localStorage.getItem('velia-voice-rate')) || 1;} catch {}
  rate = Math.max(.7, Math.min(1.4, rate));
  const node = (tag, text, cls) => {const n = document.createElement(tag); if(text)n.textContent=text;if(cls)n.className=cls;return n;};
  const dialog = node('dialog', '', 'voice-dialog'); dialog.setAttribute('aria-labelledby','voice-title');
  const title = node('h2', 'Разговор с Велией'); title.id='voice-title';
  const state = node('p', 'Нажми «Начать», чтобы включить микрофон.', 'voice-status'); state.setAttribute('role','status');
  const orb = node('div', 'V', 'voice-orb'); orb.setAttribute('aria-hidden','true');
  const transcript = node('details', '', 'voice-transcript');
  transcript.append(node('summary','Субтитры'));
  const captions = node('div', '', 'voice-captions');
  const heard = node('p', 'Здесь появятся твои слова.'); const reply = node('p', 'Ответ останется в диалоге.');
  captions.append(node('span','Ты'),heard,node('span','Велия'),reply);transcript.append(captions);
  const start = node('button','Начать'), close = node('button','Завершить');
  const controls=node('div','','voice-controls');controls.append(start,close);
  const voiceSelectors=new Set();
  const voiceHint=node('p','','voice-hint');
  function refreshVoices(){
    const choices=availableVoices(synth?.getVoices()||[],language);
    if(voiceURI&&!choices.some(v=>v.voiceURI===voiceURI))voiceURI='';
    for(const select of voiceSelectors){if(!select.isConnected&&select.dataset.mounted){voiceSelectors.delete(select);continue;}select.replaceChildren();const def=node('option','Автоматически');def.value='';select.append(def);for(const v of choices){const o=node('option',v.name+' · '+v.lang);o.value=v.voiceURI;select.append(o);}select.value=voiceURI;select.dataset.mounted='true';}
    voiceHint.textContent=choices.length===1?'На этом устройстве доступен один голос для выбранного языка.':choices.length===0?'Используется голос браузера по умолчанию.':'';
    voiceHint.hidden=!voiceHint.textContent;
  }
  function voiceControl(){const label=node('label','Голос Велии','voice-profile'), select=node('select');select.setAttribute('aria-label','Голос');voiceSelectors.add(select);select.onchange=()=>{stop();voiceURI=select.value;save();refreshVoices();};label.append(select);return label;}
  const soundTest=node('button','Проверить звук','voice-sound-test');soundTest.onclick=()=>speak('Привет! Я Велия. Рада тебя слышать.');soundTest.disabled=!synth;
  dialog.append(title,orb,state,voiceControl(),voiceHint,soundTest,controls,transcript,node('p','Ответы сохраняются в диалоге.','voice-hint'));
  document.body.append(dialog);refreshVoices();synth?.addEventListener('voiceschanged',refreshVoices);
  const status = (text, phase='idle') => {state.textContent=text;dialog.dataset.phase=phase;start.textContent=active?'Пауза':'Начать';};
  const save=()=>{try {localStorage.setItem('velia-voice-language',language);localStorage.setItem('velia-voice-uri',voiceURI);localStorage.setItem('velia-voice-rate',String(rate));}catch{}};
  let streamText='',streamCursor=0,streamQueue=[],streamDone=false,streamBlocked=false;
  function stop(){streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;clearTimeout(waitingTimer);if(pending)reply.textContent='Ответ появится в диалоге.';deferredAnswer=null;active=false;pending=false;epoch++;speechEpoch++;clearTimeout(timer);clearTimeout(speechTimer);currentUtterance=null;const old=recognition;recognition=null;old?.abort();synth?.cancel();status('Разговор на паузе.');}
  close.onclick=()=>{stop();dialog.close();};dialog.addEventListener('cancel',stop);dialog.addEventListener('close',stop);
  function later(token, delay=400){clearTimeout(timer);timer=setTimeout(()=>{if(active&&token===epoch)listen();},delay);}
  function listen(){
    if(!active||pending||!Recognition||document.hidden)return;
    if(busy()){later(epoch);return;}
    const token=epoch, r=new Recognition();recognition=r;r.lang=language;r.interimResults=true;r.continuous=false;r.maxAlternatives=1;
    let final='', interim='', failed=false;
    status('Слушаю тебя…','listening');
    const valid=()=>active&&token===epoch&&recognition===r;
    r.onresult=e=>{if(!valid())return;final='';interim='';for(const result of Array.from(e.results)){if(result.isFinal)final+=result[0].transcript+' ';else interim+=result[0].transcript+' ';}heard.textContent=(final+interim).trim()||'Слушаю…';if(final.trim())r.stop?.();};
    r.onerror=e=>{if(!valid())return;if(e.error==='no-speech')return;failed=true;active=false;status(({ 'not-allowed':'Разреши микрофон в настройках сайта и нажми «Начать».', 'service-not-allowed':'Браузер запретил распознавание речи.', 'audio-capture':'Микрофон не найден или занят другим приложением.', network:'Сервис распознавания недоступен. Проверь соединение.', 'language-not-supported':'Этот язык не поддерживается распознаванием браузера.' })[e.error]||'Не удалось распознать речь. Нажми «Начать» для повтора.','error');};
    r.onend=async()=>{if(!valid())return;recognition=null;if(failed)return;const text=final.trim();if(!text){status('Жду твою реплику…','listening');later(token,800);return;}pending=true;streamText='';streamCursor=0;streamQueue=[];streamDone=false;streamBlocked=false;heard.textContent=text;reply.textContent='Готовлю ответ…';status('Велия готовит ответ…','thinking');waitingTimer=setTimeout(()=>{if(pending&&active)status('Ответ задерживается. Запрос ещё выполняется…','thinking');},20000);try{await send(text);if(pending&&deferredAnswer===null&&active&&token===epoch)failure();}catch{if(token===epoch)failure();}};
    try{r.start();}catch{recognition=null;active=false;status('Микрофон недоступен. Проверь разрешение и повтори.','error');}
  }
  start.onclick=()=>{if(active){stop();return;}if(!Recognition){toast('Распознавание речи недоступно. Попробуй Chrome или Edge.');return;}if(busy()){toast('Дождись завершения ответа.');return;}stop();if(synth){const unlock=new SpeechSynthesisUtterance('.');unlock.volume=0;unlock.lang=language;synth.speak(unlock);synth.resume?.();}active=true;listen();};
  function speak(text, resume=false){
    if(!resume)stop();
    if(!synth){if(resume){active=false;status('Озвучивание недоступно. Ответ сохранён в диалоге.','error');}else toast('Озвучивание недоступно в этом браузере.');return;}
    const token=++speechEpoch, conversation=epoch, chunks=speechChunks(text);let index=0;synth.cancel();
    const next=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);currentUtterance=null;if(index>=chunks.length){if(resume&&active&&conversation===epoch){status('Жду твою реплику…','listening');later(conversation);}return;}
      const part=chunks[index++], utterance=new SpeechSynthesisUtterance(part);utterance.lang=language;utterance.rate=rate;utterance.pitch=1;utterance.volume=1;
      utterance.voice=voiceChoice(synth.getVoices(),language,voiceURI);
      if(resume){reply.textContent=part;status('Велия говорит…','speaking');}
      utterance.onend=next;const fail=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);speechEpoch++;currentUtterance=null;active=false;synth.cancel();status('Озвучивание прервалось. Полный ответ сохранён в диалоге.','error');};utterance.onerror=fail;currentUtterance=utterance;speechTimer=setTimeout(fail,45000);try{synth.speak(utterance);}catch{fail();}
    };next();
  }
  function pumpStream(){
    if(!active||streamBlocked||currentUtterance||document.hidden)return;
    if(!streamQueue.length){if(streamDone){status('Жду твою реплику…','listening');later(epoch);}else status('Велия готовит продолжение…','thinking');return;}
    if(!synth){failure(true);return;}
    const part=streamQueue.shift(),token=speechEpoch,utterance=new SpeechSynthesisUtterance(part);
    utterance.lang=language;utterance.rate=rate;utterance.pitch=1;utterance.volume=1;utterance.voice=voiceChoice(synth.getVoices(),language,voiceURI);
    currentUtterance=utterance;reply.textContent=part;status('Велия говорит…','speaking');
    utterance.onend=()=>{if(token!==speechEpoch)return;clearTimeout(speechTimer);currentUtterance=null;pumpStream();};
    const fail=()=>{if(token!==speechEpoch)return;failure(true);status('Озвучивание прервалось. Ответ сохранён в диалоге.','error');};
    utterance.onerror=fail;speechTimer=setTimeout(fail,45000);try{synth.speak(utterance);}catch{fail();}
  }
  function update(text,final=false){
    if(!active||(!pending&&!final)||streamBlocked)return;
    text=String(text||'');
    if(streamCursor&&text.slice(0,streamCursor)!==streamText.slice(0,streamCursor)){
      streamBlocked=true;streamQueue=[];speechEpoch++;clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();return;
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
  function failure(force=false){clearTimeout(waitingTimer);if(pending||force){pending=false;deferredAnswer=null;reply.textContent='Ответ не получен. Попробуй ещё раз.';active=false;speechEpoch++;clearTimeout(speechTimer);currentUtterance=null;synth?.cancel();status('Ответ не получен. Проверь диалог и нажми «Начать» для повтора.','error');}}
  function settings(parent){
    parent.append(node('p','Распознавание и озвучивание работают через браузер и голоса устройства. Набор голосов зависит от системы; распознаванию может требоваться интернет.','feature-notice'));
    const panel=node('div','','feature-form');parent.append(panel);panel.append(voiceControl());refreshVoices();
    const label=node('label','Язык разговора'), languages=node('select');languages.setAttribute('aria-label','Язык разговора');
    const options=[['ru-RU','Русский'],['en-US','English'],['tr-TR','Türkçe'],['de-DE','Deutsch'],['fr-FR','Français'],['es-ES','Español']];if(!options.some(([id])=>id===language))options.push([language,language]);
    for(const [id,name] of options){const o=node('option',name);o.value=id;languages.append(o);}languages.value=language;languages.onchange=()=>{stop();language=languages.value;save();refreshVoices();};label.append(languages);panel.append(label);
    const speed=node('label','Скорость речи'), select=node('select');select.setAttribute('aria-label','Скорость речи');for(const [value,name] of [[.8,'Медленнее'],[1,'Обычная'],[1.2,'Быстрее']]){const o=node('option',name);o.value=value;select.append(o);}select.value=String(rate);select.onchange=()=>{stop();rate=Number(select.value);save();};speed.append(select);panel.append(speed);
    const test=node('button','Послушать голос');test.disabled=!synth;test.onclick=()=>speak('Привет! Я Велия. Давай обсудим твою идею.');panel.append(test);
    const open=node('button','Открыть голосовой разговор','feature-primary');open.onclick=()=>dialog.showModal();open.disabled=!Recognition;panel.append(open);
    if(!Recognition)panel.append(node('p','Этот браузер не поддерживает распознавание. Озвучивание ответов доступно отдельно.','feature-notice'));
  }
  document.addEventListener('visibilitychange',()=>{
    if(!active)return;
    if(document.hidden){if(pending){synth?.pause?.();return;}stop();status('Разговор приостановлен: страница была свёрнута.');}
    else {synth?.resume?.();if(deferredAnswer!==null){const answer=deferredAnswer;deferredAnswer=null;complete(answer);}else if(pending)update(streamText);pumpStream();}
  });
  return {settings,update,complete,failure,speak,open:()=>dialog.showModal(),stop};
}
