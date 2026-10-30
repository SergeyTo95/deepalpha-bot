// One same-origin, cookie-authenticated WebSocket for one answer's speech jobs.
export function createSpeechChannel({fallback,Socket=globalThis.WebSocket,location=globalThis.location}) {
 let socket=null,opening=null,closed=false,sequence=0;
 const jobs=new Map();
 function finish(job,error=null){
  if(!jobs.has(job.id))return;
  jobs.delete(job.id);job.signal?.removeEventListener('abort',job.abort);
  if(error){try{job.controller?.error(error);}catch{}job.resolve(null);}
  else if(job.streaming){job.controller.close();}
  else job.resolve({blob:new Blob(job.parts,{type:'audio/mpeg'})});
 }
 function fail(){
  for(const job of [...jobs.values()]){
   if(!job.started&&!closed&&!job.signal?.aborted){
    jobs.delete(job.id);job.signal?.removeEventListener('abort',job.abort);
    Promise.resolve().then(()=>job.signal?.aborted?null:fallback(job.fields,job.signal)).then(job.resolve,()=>job.resolve(null));
   }else finish(job,new Error('tts_unavailable'));
  }
 }
 function open(){
  if(opening)return opening;
  opening=new Promise((resolve,reject)=>{
   if(!Socket||closed){reject(Error('tts_unavailable'));return;}
   const url=new URL('/web-api/v1/voice/stream',location.href);url.protocol=url.protocol==='https:'?'wss:':'ws:';
   socket=new Socket(url.href,'velia-voice-v1');socket.binaryType='arraybuffer';
   const timer=setTimeout(()=>{reject(Error('tts_unavailable'));socket.close();},5000);
   socket.onopen=()=>{clearTimeout(timer);resolve();};
   socket.onerror=()=>{clearTimeout(timer);reject(Error('tts_unavailable'));fail();};
   socket.onclose=()=>{clearTimeout(timer);reject(Error('tts_unavailable'));fail();};
   socket.onmessage=event=>{
    try{
     if(typeof event.data==='string'){
      if(event.data.length>8192)throw Error();
      const message=JSON.parse(event.data),job=jobs.get(message.id);if(!job)return;
      if(message.type==='start'){
       if(job.started)throw Error();job.started=true;
       if(job.streaming){
        const body=new ReadableStream({start:c=>{job.controller=c;},cancel:()=>job.abort()});
        job.resolve({response:new Response(body,{headers:{'Content-Type':'audio/mpeg','X-Velia-Audio-Stream':'1'}})});
       }
      }else if(message.type==='end'){
       if(!job.started||!job.size)throw Error();finish(job);
      }else if(message.type==='error')finish(job,Error('tts_unavailable'));
      else throw Error();
     }else{
      const data=new Uint8Array(event.data);if(data.length<=4)throw Error();
      const id=new DataView(data.buffer,data.byteOffset,4).getUint32(0),job=jobs.get(id);if(!job)return;
      if(!job.started)throw Error();job.size+=data.length-4;if(job.size>2*1024*1024)throw Error();
      const audio=data.slice(4);if(job.streaming)job.controller.enqueue(audio);else job.parts.push(audio);
     }
    }catch{socket.close();fail();}
   };
  });
  return opening;
 }
 return {
  prepare(){open().catch(()=>{});},
  request(fields,{signal,streaming=true}={}){
   if(closed||signal?.aborted||jobs.size>=3)return Promise.resolve(null);
   return new Promise(resolve=>{
    const job={id:++sequence,fields,signal,streaming,resolve,parts:[],size:0,started:false};
    job.abort=()=>{if(socket?.readyState===1&&!closed)socket.send(JSON.stringify({type:'cancel',id:job.id}));finish(job,new DOMException('Cancelled','AbortError'));};
    jobs.set(job.id,job);signal?.addEventListener('abort',job.abort,{once:true});
    open().then(()=>{if(jobs.has(job.id)&&!closed)socket.send(JSON.stringify({type:'speak',id:job.id,...fields}));}).catch(()=>fail());
   });
  },
  close(){closed=true;for(const job of [...jobs.values()])finish(job,new DOMException('Cancelled','AbortError'));socket?.close();},
 };
}

export function prefetchDepth(remainingSeconds,readyMs) {
 return Number.isFinite(remainingSeconds)&&remainingSeconds>Math.max(.5,readyMs/1000)*2+.5?2:1;
}
