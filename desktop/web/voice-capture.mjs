// Feature detection, rather than device names: Safari can record AAC/MP4,
// Chromium/Firefox usually offer Opus. Audio stays in memory until submitted.
export function recordingType(Recorder=globalThis.MediaRecorder) {
 if(!Recorder)return '';
 return ['audio/webm;codecs=opus','audio/mp4','audio/ogg;codecs=opus']
  .find(type=>Recorder.isTypeSupported?.(type))||'';
}
export function canRecord() {
 return !!(globalThis.MediaRecorder&&globalThis.navigator?.mediaDevices?.getUserMedia);
}
export function createVoiceCapture({ready,error,changed=()=>{},devices=globalThis.navigator?.mediaDevices,
 Recorder=globalThis.MediaRecorder,send=globalThis.fetch,setTimer=setTimeout,clearTimer=clearTimeout}) {
 let serial=0,stream=null,recorder=null,upload=null,timer=null,recording=false;
 function release(){clearTimer(timer);timer=null;stream?.getTracks().forEach(track=>track.stop());stream=null;}
 function cancel(){serial++;upload?.abort();upload=null;const old=recorder;recorder=null;recording=false;release();if(old?.state==='recording')old.stop();changed(false);}
 async function start(language) {
  cancel();const turn=serial;let chunks=[],size=0;
  try{
   const captured=await devices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
   if(turn!==serial){captured.getTracks().forEach(track=>track.stop());return;}
   stream=captured;const type=recordingType(Recorder);
   recorder=new Recorder(stream,{...(type?{mimeType:type}:{}),audioBitsPerSecond:64000});
   const current=recorder;
   current.ondataavailable=e=>{
    if(turn!==serial||!e.data?.size)return;
    size+=e.data.size;
    if(size>1024*1024){cancel();error('too_large');return;}
    chunks.push(e.data);
   };
   current.onerror=()=>{if(turn===serial){cancel();error('capture');}};
   current.onstop=async()=>{
    if(turn!==serial)return;
    recorder=null;recording=false;release();changed(false);
    if(!size){error('empty');return;}
    const controller=new AbortController();upload=controller;
    timer=setTimer(()=>controller.abort(),20000);
    try{
     const blob=new Blob(chunks,{type:current.mimeType||type});chunks=[];
     const response=await send('/web-api/v1/voice/transcribe?language='+encodeURIComponent(language.split('-')[0]),
      {method:'POST',credentials:'same-origin',headers:{'X-Velia-Request':'1','Content-Type':blob.type},body:blob,signal:controller.signal});
     if(!response.ok)throw Error(response.status===429?'limit':response.status===503?'unavailable':'transcribe');
     const data=await response.json();if(turn===serial)ready(String(data.text||''));
    }catch(e){if(turn===serial)error(controller.signal.aborted?'timeout':e.message);}
    finally{if(turn===serial){clearTimer(timer);timer=null;upload=null;}}
   };
   current.start(1000);recording=true;changed(true);
   // Never silently submit a truncated utterance at the recording limit.
   timer=setTimer(()=>{if(turn===serial){cancel();error('too_long');}},45000);
  }catch(e){if(turn===serial){cancel();error(e.name==='NotAllowedError'?'permission':'capture');}}
 }
 function finish(){if(recording&&recorder?.state==='recording'){recording=false;clearTimer(timer);changed(false);recorder.stop();}}
 return {start,finish,cancel,get recording(){return recording;}};
}
