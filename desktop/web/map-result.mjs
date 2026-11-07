import {loadMapLibrary} from './map.mjs';
import {validMapData,routeUrl} from './map-agent.mjs';
export function mapResultNode(data,{signal,autoOpen=false,toast=()=>{},openPlaces=()=>{},loadLibrary=loadMapLibrary}){
  if(!validMapData(data)||!data.display.length)return null;
  const node=(tag,text)=>{const e=document.createElement(tag);if(text)e.textContent=text;return e;};
  const root=node('section');root.className='chat-map-result';root.setAttribute('aria-label','Места из поручения');
  const places=data.display.map(id=>data.places.find(p=>p.ref===id));
  root.append(node('strong',data.route?'Точки поездки':'Места на карте'));
  const list=node('ol');for(const place of places){const item=node('li'),title=node('strong',place.title);item.append(title,node('p',place.address));if(place.savedId)item.append(node('small','Сохранено в моих местах'));list.append(item);}root.append(list);
  const actions=node('div');actions.className='feature-actions';const toggle=node('button','Показать карту');toggle.type='button';toggle.setAttribute('aria-expanded','false');
  const saved=node('button','Мои места');saved.type='button';saved.onclick=()=>openPlaces();actions.append(toggle,saved);
  if(data.route){const route=node('a','Открыть маршрут в навигаторе');route.href=routeUrl(data.route.ids.map(id=>data.places.find(p=>p.ref===id)),data.route.mode);route.target='_blank';route.rel='noopener noreferrer';actions.append(route);root.append(node('p','Порядок точек задан в поручении. Дороги и время поездки рассчитает навигатор.'));}
  const canvas=node('div');canvas.className='velia-map chat-map-preview';canvas.hidden=true;root.append(actions,canvas);let map=null,loading=false;
  async function show(){
    if(signal.aborted||loading)return;
    if(map){canvas.hidden=!canvas.hidden;toggle.textContent=canvas.hidden?'Показать карту':'Скрыть карту';toggle.setAttribute('aria-expanded',String(!canvas.hidden));if(!canvas.hidden)map.invalidateSize();return;}
    loading=true;toggle.disabled=true;
    try{
      const L=await loadLibrary();if(signal.aborted)return;canvas.hidden=false;
      map=L.map(canvas,{scrollWheelZoom:false});map.attributionControl.setPrefix(false);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,updateWhenIdle:true,keepBuffer:1,referrerPolicy:'strict-origin-when-cross-origin',attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'}).addTo(map);
      places.forEach((place,index)=>{const popup=node('div');popup.append(node('strong',place.title),node('p',place.address));L.marker([place.lat,place.lon],{icon:L.divIcon({className:'map-pin map-pin-number',html:'<span>'+(index+1)+'</span>',iconSize:[24,24],iconAnchor:[12,24]})}).addTo(map).bindPopup(popup);});
      if(places.length===1)map.setView([places[0].lat,places[0].lon],14);else map.fitBounds(places.map(p=>[p.lat,p.lon]),{padding:[24,24],maxZoom:15});
      toggle.textContent='Скрыть карту';toggle.setAttribute('aria-expanded','true');
    }catch{if(!signal.aborted){map?.remove();map=null;canvas.hidden=true;toast('Не удалось загрузить карту. Адреса доступны в ответе.');}}
    finally{loading=false;if(!signal.aborted)toggle.disabled=false;}
  }
  toggle.onclick=show;signal.addEventListener('abort',()=>map?.remove(),{once:true});
  if(autoOpen)queueMicrotask(()=>{if(root.isConnected&&!signal.aborted)show();});
  return root;
}
