import test from 'node:test';
import assert from 'node:assert/strict';
import {speechChunks,availableVoices,voiceChoice,speechReady,isRemoteVoice,recognitionLanguage,isVoiceEndCommand,conversationLanguages,fastDeviceVoice,automaticVoice,reconcileSpeech} from '../voice.mjs';

test('canonical speech reconciliation keeps full matching phrases and preserves changed numbers and negation',()=>{
 for(const greeting of ['Привет!','Merhaba!','مرحبا!','こんにちは！']){
  const plan=reconcileSpeech(greeting+' Новый факт.',[],greeting);
  assert.equal(plan.keepCurrent,true);assert.equal(plan.remaining,'Новый факт.');
 }
 assert.equal(reconcileSpeech('125 рублей',[], '12').keepCurrent,false);
 assert.equal(reconcileSpeech('Не отправляй 125 рублей.',['Отправляй 125 рублей.']).remaining,'Не отправляй 125 рублей.');
 const plan=reconcileSpeech('**Привет!**  🎵\nНе отправляй 125 рублей.',['Привет!'],'Отправляй 125 рублей.');
 assert.deepEqual(plan.completed,['Привет!']);assert.equal(plan.keepCurrent,false);assert.equal(plan.remaining,'Не отправляй 125 рублей.');
 assert.equal(reconcileSpeech('Привет!',['Привет!']).remaining,'');
});

test('fast voice uses only an actually reported local voice in the selected language',()=>{
 const voices=[{name:'Ава',voiceURI:'edge:en-US-AvaMultilingualNeural',lang:'ru-RU',localService:false},
  {name:'Cloud',voiceURI:'cloud',lang:'ru-RU',localService:false},
  {name:'English local',voiceURI:'en-local',lang:'en-US',localService:true},
  {name:'Russian local',voiceURI:'ru-local',lang:'ru-RU',localService:true}];
 assert.equal(fastDeviceVoice(voices,'ru-RU').voiceURI,'ru-local');
 assert.equal(fastDeviceVoice(voices,'tr-TR'),null);assert.equal(fastDeviceVoice([],'ru-RU'),null);
});
test('recognition follows saved choice then device language independently of Russian UI',()=>{
 assert.equal(recognitionLanguage('', 'ru', 'en-US'),'en-US');
 assert.equal(recognitionLanguage('tr-TR', 'ru', 'en-US'),'tr-TR');
 assert.equal(recognitionLanguage('', '', 'de-DE'),'de-DE');
 assert.equal(recognitionLanguage('', '', ''),'ru-RU');
 assert.equal(recognitionLanguage('', 'ru', 'ja-JP'),'ja-JP');
 assert.equal(recognitionLanguage('ar-EG', 'ru', 'ja-JP'),'ar-EG');
 assert.equal(recognitionLanguage('not_a_locale!', 'ru', 'hi-IN'),'hi-IN');
 assert.equal(recognitionLanguage('', 'ru', ''),'ru-RU');
});
test('language selection includes ISO languages and additional device or voice locales',()=>{
 const choices=conversationLanguages('yue-Hant-HK',['uk-UA','ar-EG'],[{lang:'hi-IN'},{lang:'ja-JP',languages:['zh-CN','ko-KR']}]);
 const ids=choices.map(([id])=>id);
 assert.ok(ids.length>150);
 for(const id of ['ar','he','hi','bn','ta','th','zh','ja','ko','sw','am','ka','uk','pt','id','yue-Hant-HK','uk-UA','ar-EG','hi-IN','ja-JP','zh-CN','ko-KR'])assert.ok(ids.includes(id),id);
 assert.equal(new Set(ids).size,ids.length);
 assert.ok(choices.every(([id,name])=>Intl.getCanonicalLocales(id)[0]===id&&name));
});
test('only an explicit whole utterance ends the call',()=>{
 for(const text of ['Велия, закончи разговор!','Заверши разговор, пожалуйста.','Пока!','Velia, end the call.','Görüşmeyi bitir.'])assert.equal(isVoiceEndCommand(text),true,text);
 for(const text of ['Не завершай разговор','Как завершить разговор?','Она сказала пока','Объясни слово пока','Привет','Закончи рассказ','Велия, расскажи как отключиться'])assert.equal(isVoiceEndCommand(text),false,text);
});
test('long speech is bounded and preserves all plain words',()=>{
  const text=Array.from({length:200},(_,i)=>`слово${i}`).join(' ');
  const chunks=speechChunks(text);
  assert.ok(chunks.length>1);assert.ok(chunks.every(x=>x.length<=240));assert.equal(chunks.join(' '),text);
});
test('speech removes code and URLs while retaining link captions',()=>{
  const result=speechChunks('**Привет** [источник](https://example.com). ```js\nsecret();\n``` https://example.com').join(' ');
  assert.ok(result.includes('Привет источник'));assert.ok(result.includes('Код сохранён в диалоге.'));assert.ok(!result.includes('secret'));assert.ok(!result.includes('https://'));
});
test('empty reply and unclosed code are handled safely',()=>{
  assert.deepEqual(speechChunks('  '),[]);assert.deepEqual(speechChunks('```js\nprivate()'),['Код сохранён в диалоге.']);
  assert.ok(speechChunks('x'.repeat(900)).every(x=>x.length<=240));
});
test('punctuation-only streamed tails never trigger speech synthesis',()=>{
 for(const text of ['...', ' — ', ',;:', '😊!', '   .'])assert.deepEqual(speechChunks(text),[]);
 assert.deepEqual(speechChunks('125 рублей.'),['125 рублей.']);
 assert.deepEqual(speechChunks('Привет.'),['Привет.']);
});

test('voice inventory is language scoped, deduplicated and represents real voices',()=>{
 const voices=[{name:'English',lang:'en-US',voiceURI:'en'}, {name:'Russian',lang:'ru-RU',voiceURI:'ru',localService:true}, {name:'Same',lang:'ru-RU',voiceURI:'ru'}];
 assert.equal(availableVoices(voices,'ru-RU').length,1);
 assert.equal(voiceChoice(voices,'ru-RU','').voiceURI,'ru');
 assert.equal(voiceChoice(voices,'ru-RU','en').voiceURI,'en');
 assert.equal(voiceChoice(voices,'tr-TR',''),null);
 voices.push({name:'Second Russian',lang:'ru-RU',voiceURI:'ru-second'});
 assert.equal(availableVoices(voices,'ru-RU').length,2);
 assert.notEqual(voiceChoice(voices,'ru-RU','ru').voiceURI,voiceChoice(voices,'ru-RU','ru-second').voiceURI);
});
test('emoji sequences are silent while punctuation and numbers remain',()=>{
 const text='Привет! 😊 ❤️ 👩🏽‍💻 🇹🇷 1️⃣ Цена 125 рублей.';
 assert.equal(speechChunks(text).join(' '),'Привет! Цена 125 рублей.');
 assert.deepEqual(speechChunks('😊'),[]);
});
test('streaming releases complete phrases and holds code and unfinished words',()=>{
 assert.equal(speechReady('Привет! Ещё'),7);
 assert.equal(speechReady('Думаю над ответом'),0);
 assert.equal(speechReady('```js\ncode();'),0);
 assert.equal(speechReady('Хвост',true),5);
});
test('long streamed sentences start at a complete word or clause',()=>{
 const text='Могу рассказать небольшую выдуманную историю, в которой героиня нашла необычное письмо';
 assert.equal(text.slice(0,speechReady(text)),'Могу рассказать небольшую выдуманную историю,');
 const long='Это длинный ответ без точки '+('содержательное слово '.repeat(8));
 const cut=speechReady(long);assert.ok(cut>40&&cut<=121);assert.equal(long[cut-1],' ');
 assert.equal(speechReady('Незаконченное сло'),0);
});
test('real female neural speakers are distinct and multilingual without replacing a fast device default',()=>{
 const voices=[{voiceURI:'device',name:'Device',lang:'ru-RU',localService:true},
  {voiceURI:'edge:ru-RU-SvetlanaNeural',name:'Светлана',lang:'ru-RU',gender:'female',recommended:true},
  {voiceURI:'edge:en-US-EmmaMultilingualNeural',name:'Эмма',lang:'en-US',languages:['ru-RU','tr-TR'],gender:'female'},
  {voiceURI:'edge:en-US-AvaMultilingualNeural',name:'Ава',lang:'en-US',languages:['ru-RU','tr-TR'],gender:'female'}];
 assert.equal(voiceChoice(voices,'ru-RU','').voiceURI,'device');
 assert.equal(availableVoices(voices,'ru-RU').length,4);
 assert.equal(availableVoices(voices,'tr-TR').length,2);
 assert.equal(voiceChoice(voices,'ru-RU',voices[2].voiceURI).name,'Эмма');
 assert.ok(isRemoteVoice(voices[1].voiceURI));assert.equal(isRemoteVoice('edge:ru-RU-DmitryNeural'),false);
});

test('account language overrides legacy browser choice across locales',()=>{
 assert.equal(recognitionLanguage('en-US','ru','de-DE','ru'),'ru-RU');
 assert.equal(recognitionLanguage('ru-RU','ru','en-US','tr'),'tr-TR');
 assert.equal(recognitionLanguage('ru-RU','ru','en-US','ja-JP'),'ja-JP');
 assert.equal(recognitionLanguage('ru-RU','ru','en-US','zh-Hant-TW'),'zh-Hant-TW');
 assert.equal(recognitionLanguage('ar-EG','ru','en-US','bad!'),'ar-EG');
});
test('automatic voice prefers a local language match then a real available voice',()=>{
 const voices=[{voiceURI:'cloud',name:'Cloud',lang:'ja-JP',recommended:true,localService:false},
  {voiceURI:'local',name:'Local',lang:'ja-JP',localService:true},
  {voiceURI:'english-local',name:'English local',lang:'en-US',localService:true}];
 assert.equal(automaticVoice(voices,'ja-JP').voiceURI,'local');
 assert.equal(automaticVoice(voices.filter(v=>v.voiceURI!=='local'),'ja-JP').voiceURI,'cloud');
 assert.equal(automaticVoice(voices,'ar-EG'),null);
});
