const MIME={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',webp:'image/webp',pdf:'application/pdf',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',txt:'text/plain',md:'text/plain',csv:'text/plain',json:'text/plain'};
export function prepareFile(file) {
  const ext=file.name.split('.').at(-1).toLowerCase(),type=MIME[ext];
  if(!type)throw new Error('Поддерживаются JPEG, PNG, WebP, PDF, DOCX, TXT, MD, CSV и JSON. XLSX пока недоступен.');
  if(!file.size)throw new Error('Пустой файл нельзя отправить.');
  if(file.size>15*1024*1024)throw new Error('Максимальный размер файла — 15 МБ.');
  return file.type===type?file:new File([file],file.name,{type,lastModified:file.lastModified});
}
export const fileSize = bytes => bytes<1024*1024 ? Math.ceil(bytes/1024)+' КБ' : (bytes/1024/1024).toFixed(1)+' МБ';
export function setupFileTools({toast}) {
  const dialog=document.createElement('dialog');dialog.className='file-dialog';dialog.setAttribute('aria-labelledby','file-view-title');
  const header=document.createElement('div');header.className='file-view-header';
  const title=document.createElement('h2');title.id='file-view-title';
  const close=document.createElement('button');close.type='button';close.textContent='Закрыть';close.onclick=()=>dialog.close();header.append(title,close);
  const body=document.createElement('div');body.className='file-view-body';const actions=document.createElement('div');actions.className='feature-actions';dialog.append(header,body,actions);document.body.append(dialog);
  let owned=[],ticket=0;const cache=new Map();
  const reset=()=>{ticket++;for(const url of owned)URL.revokeObjectURL(url);owned=[];body.replaceChildren();actions.replaceChildren();};dialog.addEventListener('close',reset);
  const begin=name=>{reset();title.textContent=name.slice(0,160); if(!dialog.open)dialog.showModal();return ticket;};
  const link=(url,name,label)=>{const a=document.createElement('a');a.textContent=label;a.href=url;a.download=name;a.target='_blank';a.rel='noopener';actions.append(a);};
  function image(url,name){const img=document.createElement('img');img.src=url;img.alt=name;body.append(img);const b=document.createElement('button');b.type='button';b.textContent='Увеличить';b.setAttribute('aria-pressed','false');b.onclick=()=>{const zoom=img.classList.toggle('zoomed');b.textContent=zoom?'По размеру окна':'Увеличить';b.setAttribute('aria-pressed',String(zoom));};actions.append(b);img.onerror=()=>{body.replaceChildren();const p=document.createElement('p');p.textContent='Не удалось показать изображение.';body.append(p);};}
  async function previewFile(file){const current=begin(file.name);const p=document.createElement('p');p.textContent=fileSize(file.size)+' · '+(file.type||'Файл');body.append(p);
    if(file.type.startsWith('image/')){const url=URL.createObjectURL(file);owned.push(url);image(url,file.name);link(url,file.name,'Скачать');}
    else if(file.type==='text/plain'){const text=await file.slice(0,100000).text();if(current!==ticket)return;const pre=document.createElement('pre');pre.textContent=text;body.append(pre);const copy=document.createElement('button');copy.type='button';copy.textContent='Копировать текст';copy.onclick=async()=>{try{await navigator.clipboard.writeText(text);toast('Скопировано.');}catch{toast('Не удалось скопировать. Выдели текст вручную.');}};actions.append(copy);const url=URL.createObjectURL(file);owned.push(url);link(url,file.name,'Скачать оригинал');if(file.size>100000){const note=document.createElement('p');note.textContent='Показано начало файла — до 100 КБ.';body.append(note);}}
    else if(file.type==='application/pdf'){if(await file.slice(0,5).text()!=='%PDF-'){if(current===ticket)toast('Файл не похож на PDF.');return;}if(current!==ticket)return;const url=URL.createObjectURL(file);owned.push(url);link(url,file.name,'Открыть / скачать PDF');const note=document.createElement('p');note.textContent='PDF откроется средствами браузера или устройства.';body.append(note);}
    else {const note=document.createElement('p');note.textContent='DOCX можно отправить Велии для анализа. Встроенный предпросмотр этого формата пока недоступен.';body.append(note);}
  }
  function openImage(path,name='Изображение Studio'){const url=new URL(path,location.origin);if(url.origin!==location.origin||!url.pathname.startsWith('/web-api/v1/features/media/'))return;begin(name);image(url.href,name);link(url.href,'VELIA-image','Скачать');}
  function downloadText(text,name){const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name.replace(/[\\/\x00-\x1f]/g,'_');a.hidden=true;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  function remember(id,file){cache.set(id,file);let total=[...cache.values()].reduce((n,f)=>n+f.size,0);while(total>60*1024*1024||cache.size>12){const key=cache.keys().next().value;total-=cache.get(key).size;cache.delete(key);}}
  function clear(){cache.clear();if(dialog.open)dialog.close();else reset();}
  return {previewFile,openImage,downloadText,remember,get:id=>cache.get(id),clear};
}
