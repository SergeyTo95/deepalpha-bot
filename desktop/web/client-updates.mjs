// Detect a newer deployed UI without reloading unsaved work or active requests.
export function setupClientUpdates({version,request,show,refresh,blocked=()=>false,toast=()=>{},document:doc=globalThis.document,window:win=globalThis.window}){
  let stale=false,stopped=false,loading=null,timer=null;
  const valid=v=>typeof v==='string'&&/^[a-f0-9]{64}$/u.test(v);
  async function reload(){
    if(blocked()){toast('Скопируй или очисти черновик, закрой открытый раздел и заверши текущий ввод перед обновлением.');return;}
    try{await refresh();}catch{toast('Не удалось подготовить обновление. Сохрани важный текст и обнови страницу вручную.');}
  }
  async function check(){
    if(stopped||!valid(version)||doc.hidden)return;
    if(loading)return loading;
    const task=(async()=>{
      try{const response=await request('client-version',undefined,AbortSignal.timeout(4000));if(!response.ok)return;const value=await response.json();
        if(!stopped&&value.ok===true&&valid(value.version)&&value.version!==version&&!stale){stale=true;show(reload);}
      }catch{/* Offline and rolling-deploy failures never interrupt the conversation. */}
    })();loading=task;try{await task;}finally{if(loading===task)loading=null;clearTimeout(timer);if(!stopped)timer=setTimeout(check,60000);}
  }
  const wake=()=>{if(!doc.hidden)check();};
  doc.addEventListener('visibilitychange',wake);win?.addEventListener('pageshow',wake);win?.addEventListener('focus',wake);
  check();
  return {check,stale:()=>stale,stop:()=>{stopped=true;clearTimeout(timer);doc.removeEventListener('visibilitychange',wake);win?.removeEventListener('pageshow',wake);win?.removeEventListener('focus',wake);}};
}
