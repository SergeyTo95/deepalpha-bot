import test from 'node:test';
import assert from 'node:assert/strict';
import {mapResultNode,mapReplyText} from '../map-result.mjs';
const places=[{ref:'p0',title:'Start',address:'Antalya',lat:36.885,lon:30.708},{ref:'p1',title:'Finish',address:'Antalya',lat:36.900,lon:30.798}];
const road={provider:'openrouteservice',mode:'driving',input_coordinates:places.map(p=>[p.lon,p.lat]),order:[0,1],distance_m:15500,duration_s:1320,geometry:places.map(p=>[p.lon,p.lat]),legs:[{distance_m:15500,duration_s:1320}],traffic:false,optimized:false,calculated_at:1794400000,license:'CC-BY-SA-4.0'};
const data={version:1,places,display:['p0','p1'],route:{ids:['p0','p1'],mode:'driving',road}};
const credit='\n\n© [openrouteservice by HeiGIT](https://heigit.org/) · [OpenStreetMap](https://www.openstreetmap.org/copyright) · [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)';
test('route presentation omits duplicate reply credit while canonical copy/export content stays intact',()=>{
  const content='По дорогам: 15.5 км'+credit;
  assert.equal(mapReplyText(content,data),'По дорогам: 15.5 км');
  assert.ok(content.endsWith(credit));
  assert.equal(mapReplyText(content,null),content);
  assert.equal(mapReplyText(content,{...data,route:null}),content);
  assert.equal(mapReplyText('Другой ответ © OpenStreetMap',data),'Другой ответ © OpenStreetMap');
});
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.hidden=false;this.attributes={};}
  append(...children){this.children.push(...children);}
  get childNodes(){return this.children;}
  setAttribute(key,value){this.attributes[key]=value;}
}
const nodes=root=>[root,...root.children.flatMap(nodes)];
test('credit remains visible once while map is closed, open, reopened or unavailable',async()=>{
  const previous=globalThis.document;globalThis.document={createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('text'),{textContent:text})};
  try{
    let fail=false;const attributions=[];
    const map={attributionControl:{setPrefix(){},addAttribution:text=>attributions.push(text)},fitBounds(){},invalidateSize(){},remove(){}};
    const layer={addTo:()=>layer,bindPopup:()=>layer};
    const loadLibrary=async()=>{if(fail)throw Error('offline');return {map:()=>map,tileLayer:(_url,options)=>{attributions.push(options.attribution);return layer;},marker:()=>layer,divIcon:()=>{},polyline:()=>layer};};
    const root=mapResultNode(data,{signal:new AbortController().signal,loadLibrary});
    const credit=nodes(root).find(e=>e.className==='map-attribution'),toggle=nodes(root).find(e=>e.tag==='button'&&e.textContent==='Показать карту');
    assert.equal(credit.hidden,false);assert.equal(nodes(root).filter(e=>e.className==='map-attribution').length,1);
    await toggle.onclick();assert.equal(credit.hidden,true);assert.ok(attributions.some(a=>a.includes('OpenStreetMap')));assert.ok(attributions.some(a=>a.includes('openrouteservice')&&a.includes('CC BY-SA')));
    await toggle.onclick();assert.equal(credit.hidden,false);await toggle.onclick();assert.equal(credit.hidden,true);
    fail=true;const failed=mapResultNode(data,{signal:new AbortController().signal,loadLibrary});
    await nodes(failed).find(e=>e.tag==='button'&&e.textContent==='Показать карту').onclick();assert.equal(nodes(failed).find(e=>e.className==='map-attribution').hidden,false);
  }finally{globalThis.document=previous;}
});
