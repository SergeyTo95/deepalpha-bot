import {editEntry} from './notebook-ui.mjs';
export function distanceKm(a,b){const r=value=>value*Math.PI/180,dlat=r(b.lat-a.lat),dlon=r(b.lon-a.lon);return 6371*2*Math.asin(Math.min(1,Math.sqrt(Math.sin(dlat/2)**2+Math.cos(r(a.lat))*Math.cos(r(b.lat))*Math.sin(dlon/2)**2)));}
export function navigationUrl(place,from){
  const target=new URL('https://www.google.com/maps/dir/');target.searchParams.set('api','1');target.searchParams.set('destination',place.lat+','+place.lon);
  if(from)target.searchParams.set('origin',from.lat+','+from.lon);
  return target.href;
}
export async function searchPlaces(query,signal){
  const response=await fetch('/web-api/v1/map/search',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Velia-Request':'1'},body:JSON.stringify({query}),signal});
  const result=await response.json();
  if(!response.ok)throw Error(response.status===429?'Поиск занят. Попробуй через минуту.':'Не удалось найти место. Попробуй уточнить адрес.');
  return result.places;
}
let leaflet;
export async function loadMapLibrary(){
  if(window.L)return window.L;
  if(!leaflet)leaflet=new Promise((resolve,reject)=>{
    const css=document.createElement('link');css.rel='stylesheet';css.href='/web/leaflet-vendor.css';document.head.append(css);
    const script=document.createElement('script');script.src='/web/leaflet-vendor.js';script.onload=()=>resolve(window.L);script.onerror=()=>{leaflet=null;reject(Error('Не удалось открыть карту. Обнови страницу.'));};document.head.append(script);
  });
  return leaflet;
}
export async function mountMap(parent,{notebook,toast,signal,draft={},loadLibrary=loadMapLibrary}){
  const node=(tag,text,cl)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cl)e.className=cl;return e;};
  const button=(title,action)=>{const b=node('button',title);b.type='button';b.onclick=action;return b;};
  const toolbar=node('form',null,'map-toolbar'),query=node('input');query.type='search';query.placeholder='Место или адрес';query.required=true;query.minLength=2;query.maxLength=200;query.setAttribute('aria-label','Найти на карте');query.value=draft.query||'';
  const submit=node('button','Найти');submit.type='submit';toolbar.append(query,submit);
  const status=node('p','Найди место или выбери точку на карте.','notebook-hint'),canvas=node('div',null,'velia-map');canvas.setAttribute('aria-label','Карта мест');canvas.setAttribute('role','region');
  canvas.hidden=true;
  const results=node('div',null,'map-results'),saved=node('div'),editor=node('div');
  parent.append(toolbar,status,canvas,results,node('h2','Мои места'),saved,editor);
  let map,markers,savedMarkers,L,position=null,adding=false,focused=false;
  const navigate=(place,from)=>{const link=node('a','Маршрут в навигаторе');link.href=navigationUrl(place,from);link.target='_blank';link.rel='noopener noreferrer';return link;};
  function edit(place){editEntry(editor,{kind:'place',title:place.title||'Моё место',content:place.address||'',tags:[],...place},{notebook,toast,signal,onDone:()=>{editor.replaceChildren();drawSaved();}});}
  function show(place){
    if(!map)return;canvas.hidden=false;map.invalidateSize();focused=true;map.setView([place.lat,place.lon],15);
    markers.clearLayers();
    const marker=L.marker([place.lat,place.lon],{icon:L.divIcon({className:'map-pin',html:'<span></span>',iconSize:[22,22],iconAnchor:[11,22]})}).addTo(markers);
    const popup=node('div');popup.append(node('strong',place.title),node('p',place.address||place.content||''),button('Сохранить место',()=>edit(place)),navigate(place,position));marker.bindPopup(popup).openPopup();
  }
  async function drawSaved(){
    try{
      const places=(await notebook.load()).filter(entry=>entry.kind==='place');if(signal.aborted)return;saved.replaceChildren();
      if(!places.length)saved.append(node('p','Попроси Велию в общем чате: «Найди адрес на карте» или «Покажи мои сохранённые места». Здесь появятся сохранённые точки.','feature-empty'));
      if(map&&places.length&&!focused&&!draft.query&&draft.lat===undefined){canvas.hidden=false;map.invalidateSize();if(places.length===1)map.setView([places[0].lat,places[0].lon],14);else map.fitBounds(places.map(p=>[p.lat,p.lon]),{padding:[24,24],maxZoom:15});focused=true;}
      if(savedMarkers)savedMarkers.clearLayers();
      for(const place of places){
        if(savedMarkers){
          const pin=L.marker([place.lat,place.lon],{icon:L.divIcon({className:'map-pin',html:'<span></span>',iconSize:[22,22],iconAnchor:[11,22]})}).addTo(savedMarkers);
          const popup=node('div');popup.append(node('strong',place.title),node('p',place.content||''),button('Изменить',()=>edit(place)),navigate(place,position));pin.bindPopup(popup);
        }
        const row=node('div',null,'map-place');row.append(button(place.title,()=>show(place)),navigate(place,position),button('Изменить',()=>edit(place)));saved.append(row);
      }
      if(places.length>=2){
        const route=node('div',null,'map-route'),from=node('select'),to=node('select');from.setAttribute('aria-label','Откуда');to.setAttribute('aria-label','Куда');
        for(const place of places){for(const select of [from,to]){const option=node('option',place.title);option.value=place.id;select.append(option);}}
        to.selectedIndex=1;const detail=node('span'),links=node('span');route.append(from,to,detail,links);saved.append(route);
        const update=()=>{const a=places.find(p=>p.id===from.value),b=places.find(p=>p.id===to.value);detail.textContent='По прямой: '+distanceKm(a,b).toFixed(1)+' км';links.replaceChildren(navigate(b,a));};from.onchange=to.onchange=update;update();
      }
    }catch(error){if(!signal.aborted)saved.replaceChildren(node('p',error.message,'feature-notice'));}
  }
  toolbar.onsubmit=async event=>{
    event.preventDefault();submit.disabled=true;results.replaceChildren();
    try{const places=await searchPlaces(query.value.trim(),signal);if(signal.aborted)return;
      status.textContent=places.length?'Выбери нужное место.':'Мест не найдено. Уточни адрес или название.';
      for(const place of places){const row=node('div',null,'map-place');row.append(button(place.title+' · '+place.address,()=>show(place)),button('Сохранить',()=>edit(place)));results.append(row);}
      if(places.length)show(places[0]);
    }catch(error){if(!signal.aborted)status.textContent=error.message;}finally{if(!signal.aborted)submit.disabled=false;}
  };
  const locate=button('Моё местоположение',()=>{
    if(!navigator.geolocation){toast('Геолокация недоступна в этом браузере.');return;}
    locate.disabled=true;
    navigator.geolocation.getCurrentPosition(value=>{locate.disabled=false;if(signal.aborted)return;position={lat:value.coords.latitude,lon:value.coords.longitude,title:'Моё местоположение'};show(position);drawSaved();},()=>{locate.disabled=false;if(!signal.aborted)toast('Не удалось определить место. Можно выбрать его на карте.');},{timeout:10000,maximumAge:60000});
  });
  const add=button('Выбрать точку',()=>{adding=!adding;if(adding&&map){canvas.hidden=false;map.invalidateSize();}add.textContent=adding?'Отмена выбора':'Выбрать точку';status.textContent=adding?'Нажми на нужное место на карте.':'Найди место или выбери точку на карте.';});toolbar.append(locate,add);
  try{
    L=await loadLibrary();if(signal.aborted)return;
    map=L.map(canvas,{scrollWheelZoom:false}).setView([20,0],2);map.attributionControl.setPrefix(false);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,updateWhenIdle:true,keepBuffer:1,attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',referrerPolicy:'strict-origin-when-cross-origin'}).addTo(map);
    markers=L.layerGroup().addTo(map);savedMarkers=L.layerGroup().addTo(map);map.on('click',event=>{if(!adding)return;adding=false;add.textContent='Выбрать точку';edit({lat:event.latlng.lat,lon:event.latlng.lng,title:'Моё место'});});
    signal.addEventListener('abort',()=>map.remove(),{once:true});
    if(draft.lat!==undefined)show(draft);
    if(draft.query)toolbar.dispatchEvent(new Event('submit',{cancelable:true}));
  }catch(error){if(!signal.aborted){status.textContent=error.message;canvas.hidden=true;}}
  await drawSaved();
}
