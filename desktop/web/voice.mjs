export function setupVoice({send, busy, toast}) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null, active = false, pending = false;
  const synth = window.speechSynthesis;
  let language = navigator.language || 'ru-RU', voiceURI = '';
  try {language = localStorage.getItem('velia-voice-language') || language; voiceURI = localStorage.getItem('velia-voice-uri') || '';} catch {}
  const dialog = document.createElement('dialog'); dialog.className = 'voice-dialog';
  const title = document.createElement('h2'); title.textContent = 'Голосовой разговор';
  const state = document.createElement('p'); state.setAttribute('role','status');
  const start = document.createElement('button'); start.textContent='Начать разговор';
  const close = document.createElement('button'); close.textContent='Завершить';
  dialog.append(title,state,start,close);document.body.append(dialog);
  function stop(){active=false;pending=false;recognition?.abort();recognition=null;synth?.cancel();state.textContent='Разговор завершён';}
  close.onclick=()=>{stop();dialog.close();};dialog.addEventListener('cancel',stop);
  const save=()=>{try{localStorage.setItem('velia-voice-language',language);localStorage.setItem('velia-voice-uri',voiceURI);}catch{}};
  function listen(){
    if(!active||busy()||!Recognition)return;
    recognition=new Recognition();recognition.lang=language;recognition.interimResults=true;recognition.continuous=false;
    let text='';state.textContent='Слушаю…';
    recognition.onresult=e=>{text=[...e.results].filter(r=>r.isFinal).map(r=>r[0].transcript).join(' ').trim();};
    recognition.onerror=e=>{active=false;state.textContent=e.error==='not-allowed'?'Разреши доступ к микрофону в настройках браузера.':'Не удалось распознать речь. Нажми «Начать разговор» ещё раз.';};
    recognition.onend=()=>{recognition=null;if(!active)return;if(text){pending=true;state.textContent='Велия готовит ответ…';send(text);}else{active=false;state.textContent='Речь не распознана. Попробуй ещё раз.';}};
    try{recognition.start();}catch{active=false;state.textContent='Микрофон недоступен.';}
  }
  start.onclick=()=>{if(!Recognition){toast('В этом браузере распознавание речи недоступно.');return;}if(busy()){toast('Дождись завершения ответа.');return;}stop();active=true;listen();};
  function speak(text, resume=false){
    if(!synth){toast('Озвучивание недоступно в этом браузере.');return;}
    synth.cancel();const utterance=new SpeechSynthesisUtterance(text.replace(/```[\s\S]*?```/g,'Код сохранён в диалоге.').replace(/[*#`]/g,''));utterance.lang=language;utterance.voice=synth.getVoices().find(v=>v.voiceURI===voiceURI)||null;
    if(resume)state.textContent='Велия говорит…';utterance.onend=()=>{if(resume&&active)listen();};utterance.onerror=()=>{if(resume){active=false;state.textContent='Браузер не смог озвучить ответ. Он сохранён в диалоге.';}};synth.speak(utterance);
  }
  function complete(text){if(pending&&active){pending=false;speak(text,true);}}
  function failure(){if(pending){pending=false;active=false;state.textContent='Ответ не получен. Повтори попытку.';}}
  function settings(parent){
    const p=document.createElement('p');p.textContent='Браузер использует доступные голоса устройства и своё распознавание речи. Мобильные офлайн-пакеты здесь не используются.';p.className='feature-notice';parent.append(p);const panel=document.createElement('div');panel.className='feature-form';parent.append(panel);
    const l=document.createElement('label');l.textContent='Язык речи';const input=document.createElement('input');input.value=language;input.placeholder='ru-RU / en-US / tr-TR';input.onchange=()=>{language=input.value.trim()||navigator.language;save();};l.append(input);panel.append(l);
    const voices=document.createElement('select');voices.setAttribute('aria-label','Голос');const refresh=()=>{voices.replaceChildren();const defaultVoice=document.createElement('option');defaultVoice.value='';defaultVoice.textContent='Голос устройства';voices.append(defaultVoice);for(const v of synth?.getVoices()||[]){const o=document.createElement('option');o.value=v.voiceURI;o.textContent=v.name+' · '+v.lang;voices.append(o);}voices.value=voiceURI;};refresh();synth?.addEventListener('voiceschanged',refresh,{once:true});voices.onchange=()=>{voiceURI=voices.value;save();};panel.append(voices);
    const b=document.createElement('button');b.textContent='Открыть голосовой разговор';b.onclick=()=>dialog.showModal();b.disabled=!Recognition;b.className='feature-primary';panel.append(b);
  }
  return {settings,complete,failure,speak,open:()=>dialog.showModal(),stop};
}
