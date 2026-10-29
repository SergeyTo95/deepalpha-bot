// Progressive MP3 playback is optional; other browsers keep the Blob player.
export function canStreamAudio(Source=globalThis.window?.MediaSource) {
  try{return !!Source?.isTypeSupported('audio/mpeg');}catch{return false;}
}

export function attachAudioStream(response,{signal,onFailure,Source=window.MediaSource}) {
  const source=new Source(),reader=response.body.getReader(),url=URL.createObjectURL(source);
  let cancelled=false,buffer=null,interrupt=null;
  const abortError=()=>new DOMException('Playback stopped','AbortError');
  function cancel(){
    if(cancelled)return;
    cancelled=true;signal?.removeEventListener('abort',cancel);
    interrupt?.(abortError());
    reader.cancel().catch(()=>{});
    if(buffer?.updating)try{buffer.abort();}catch{}
  }
  const wait=(target,event,action)=>new Promise((resolve,reject)=>{
    const cleanup=()=>{target.removeEventListener(event,ok);target.removeEventListener('error',fail);interrupt=null;};
    const ok=()=>{cleanup();resolve();},fail=()=>{cleanup();reject(Error('tts_unavailable'));};
    interrupt=error=>{cleanup();reject(error);};
    target.addEventListener(event,ok,{once:true});target.addEventListener('error',fail,{once:true});
    try{if(cancelled)interrupt(abortError());else action?.();}catch(error){cleanup();reject(error);}
  });
  signal?.addEventListener('abort',cancel,{once:true});
  if(signal?.aborted)cancel();
  const completion=(async()=>{
    try{
      await wait(source,'sourceopen');
      buffer=source.addSourceBuffer('audio/mpeg');
      let size=0;
      while(!cancelled){
        const {value,done}=await reader.read();if(done)break;
        if(!value?.byteLength)continue;
        size+=value.byteLength;
        if(size>2*1024*1024)throw Error('tts_unavailable');
        await wait(buffer,'updateend',()=>buffer.appendBuffer(value));
      }
      if(cancelled)return;
      if(!size)throw Error('tts_unavailable');
      source.endOfStream();
    }catch(error){
      if(!cancelled){cancel();onFailure();}
    }finally{
      signal?.removeEventListener('abort',cancel);
      try{reader.releaseLock();}catch{}
    }
  })();
  return {url,cancel,completion};
}
