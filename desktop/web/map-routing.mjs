export function validRoadRoute(route,points){
  if(!route||route.provider!=='openrouteservice'||!['driving','walking','bicycling'].includes(route.mode)||route.traffic!==false||typeof route.optimized!=='boolean'||route.license!=='CC-BY-SA-4.0')return false;
  const n=route.order?.length;
  if(!Array.isArray(route.order)||n<2||n>5||route.order[0]!==0||route.order.at(-1)!==n-1||new Set(route.order).size!==n||route.order.some(i=>!Number.isInteger(i)||i<0||i>=n))return false;
  if(!Array.isArray(route.input_coordinates)||route.input_coordinates.length!==n||route.input_coordinates.some(p=>!Array.isArray(p)||p.length!==2||!Number.isFinite(p[0])||Math.abs(p[0])>180||!Number.isFinite(p[1])||Math.abs(p[1])>90))return false;
  if(points&&(points.length!==n||points.some((p,i)=>p.lon!==route.input_coordinates[i][0]||p.lat!==route.input_coordinates[i][1])))return false;
  if(!Number.isFinite(route.distance_m)||route.distance_m<0||route.distance_m>50000000||!Number.isFinite(route.duration_s)||route.duration_s<0||route.duration_s>604800||!Number.isInteger(route.calculated_at)||route.calculated_at<=0)return false;
  return Array.isArray(route.geometry)&&route.geometry.length>=2&&route.geometry.length<=10000&&route.geometry.every(p=>Array.isArray(p)&&p.length===2&&Number.isFinite(p[0])&&Math.abs(p[0])<=180&&Number.isFinite(p[1])&&Math.abs(p[1])<=90)&&Array.isArray(route.legs)&&route.legs.length===n-1&&route.legs.every(p=>p&&Number.isFinite(p.distance_m)&&p.distance_m>=0&&Number.isFinite(p.duration_s)&&p.duration_s>=0);
}
const ERRORS={route_key_required:'Для расчёта внутри Velia ещё не настроен бесплатный ключ маршрутизации.',route_free_limit:'Достигнут бесплатный лимит маршрутизации. Платный расчёт не включается.',route_user_limit:'Лимит расчётов сейчас исчерпан. Попробуй позже.',route_mode_unavailable:'Этот транспорт пока рассчитывается только в навигаторе.',route_no_path:'Не удалось построить проезжаемый маршрут через эти точки.',route_busy:'Расчёт маршрутов занят. Попробуй немного позже.',route_access_denied:'Бесплатный сервис маршрутизации отклонил доступ. Можно открыть маршрут в навигаторе.'};
export async function requestRoadRoute(points,mode,optimize,signal){
  const response=await fetch('/web-api/v1/map/route',{method:'POST',credentials:'same-origin',signal,headers:{'Content-Type':'application/json','X-Velia-Request':'1'},body:JSON.stringify({coordinates:points.map(p=>[p.lon,p.lat]),mode,optimize})});
  const value=await response.json();
  if(!response.ok||!value.ok){const error=Error(response.status===401?'Войди в Велию для расчёта маршрута.':ERRORS[value.error]||'Расчёт дорог сейчас недоступен.');error.status=response.status;throw error;}
  if(!validRoadRoute(value.route,points)||value.route.mode!==mode||value.route.optimized!==optimize)throw Error('Не удалось проверить рассчитанный маршрут.');
  return value.route;
}
export function roadRouteText(route,language='ru'){
  const km=(route.distance_m/1000).toFixed(1),minutes=Math.ceil(route.duration_s/60),en=String(language).startsWith('en');
  return en?`Road route: ${km} km · approximately ${minutes} min, excluding traffic and time at stops. ${route.optimized?'Intermediate stops ordered by estimated travel time; start and finish fixed.':'Stop order preserved.'}`:
    `По дорогам: ${km} км · примерно ${minutes} мин, без пробок и времени на остановки. ${route.optimized?'Порядок промежуточных точек подобран по расчётному времени; начало и конец сохранены.':'Порядок точек сохранён.'}`;
}
