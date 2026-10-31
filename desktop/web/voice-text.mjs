// Keep recognition/chat/canonical reconciliation untouched. These changes apply
// only to the text handed to a speech engine, in the account's language.
export function sentenceBoundary(text) {
 const endings=/[.!?](?=\s|$)/g;let match;
 while((match=endings.exec(text))){
  const prefix=text.slice(0,match.index+1);
  // A numeric full stop at the current network boundary can still become a
  // decimal (215. -> 215.5). Wait for the next character or final completion.
  if(match[0]==='.'&&match.index===text.length-1&&/\d\.$/.test(prefix))continue;
  if(match[0]==='.'&&(
   /(?:^|\s)(?:т\.|т\.\s*е\.|т\.\s*д\.|т\.\s*п\.|т\.\s*к\.|н\.|н\.\s*э\.|руб\.|коп\.|ул\.|д\.|г\.|стр\.|им\.|рис\.|Mr\.|Mrs\.|Dr\.|Prof\.|e\.g\.|i\.e\.)$/iu.test(prefix)
   ||/(?:^|\s)\p{Lu}\.$/u.test(prefix)))continue;
  return match.index+1;
 }
 return 0;
}

// Ignore only validated thousands-group whitespace for speech reconciliation.
// Keep every digit, decimal separator, sign, word and punctuation mark. Offsets
// map the matched key back to the canonical source so its tail stays exact.
export function speechIdentity(text) {
 const skipped=new Set();
 for(const match of text.matchAll(/(^|[^\p{L}\p{N}])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+)(?![\p{L}\p{N}])/gu)){
  for(let i=0;i<match[2].length;i++)if(/[ \u00a0\u202f]/.test(match[2][i]))skipped.add(match.index+match[1].length+i);
 }
 let key='';const ends=[];
 for(let i=0;i<text.length;i++)if(!skipped.has(i)){key+=text[i];ends.push(i+1);}
 return {key,ends};
}
const dates=['первое','второе','третье','четвёртое','пятое','шестое','седьмое','восьмое','девятое','десятое','одиннадцатое','двенадцатое','тринадцатое','четырнадцатое','пятнадцатое','шестнадцатое','семнадцатое','восемнадцатое','девятнадцатое','двадцатое','двадцать первое','двадцать второе','двадцать третье','двадцать четвёртое','двадцать пятое','двадцать шестое','двадцать седьмое','двадцать восьмое','двадцать девятое','тридцатое','тридцать первое'];
const months=['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
export function pronounceSpeech(text,language='ru') {
 if(!/^ru(?:-|$)/i.test(language))return text;
 const result=text.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g,(raw,y,m,d,offset,original)=>{
  const date=new Date(Date.UTC(+y,+m-1,+d));
  if(+y<1900||+y>2199||date.getUTCFullYear()!==+y||date.getUTCMonth()!==+m-1||date.getUTCDate()!==+d)return raw;
  let day=dates[+d-1];
  const prep=original.slice(0,offset).match(/(?:^|\s)(до|после|с|от|для|около|без|к|перед)\s+$/iu)?.[1].toLowerCase();
  if(prep==='к')day=day.replace(/ое$/,'ому').replace(/ье$/,'ьему');
  else if(prep==='перед')day=day.replace(/ое$/,'ым').replace(/ье$/,'ьим');
  else if(prep)day=day.replace(/ое$/,'ого').replace(/ье$/,'ьего');
  return day+' '+months[+m-1]+' '+y+' года';
 }).replace(/(^|\s)т\.\s*е\.(?=\s|$)/giu,'$1то есть')
  .replace(/(^|\s)т\.\s*д\.(?=\s|$)/giu,'$1так далее')
  .replace(/(^|\s)т\.\s*п\.(?=\s|$)/giu,'$1тому подобное')
  .replace(/\b(\d+(?:[.,]\d+)?)\s*(?:₽|руб\.(?=\s|$))/giu,(_,number)=>{
   const n=Number(number.replace(',','.')),last=n%10,teen=n%100;
   const unit=/[.,]/.test(number)?'рубля':teen>=11&&teen<=14?'рублей':last===1?'рубль':last>=2&&last<=4?'рубля':'рублей';
   return number+' '+unit;
  });
 // Preserve the server's synthesis size bound even for abbreviation-heavy text.
 return result.length<=500?result:text;
}
