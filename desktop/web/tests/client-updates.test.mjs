import test from 'node:test';
import assert from 'node:assert/strict';
import {setupClientUpdates} from '../client-updates.mjs';
function fixture(){const listeners=new Map();return {hidden:false,addEventListener:(n,fn)=>listeners.set(n,fn),removeEventListener:n=>listeners.delete(n),listeners};}
const response=version=>({ok:true,json:async()=>({ok:true,version})});
test('a new server version shows an update control without automatically reloading',async()=>{
  const doc=fixture(),win=fixture();let offered,reloads=0;
  const api=setupClientUpdates({version:'a'.repeat(64),request:async()=>response('b'.repeat(64)),show:fn=>offered=fn,refresh:async()=>reloads++,document:doc,window:win});
  try{await api.check();assert.equal(api.stale(),true);assert.equal(reloads,0);assert.equal(typeof offered,'function');await offered();assert.equal(reloads,1);}finally{api.stop();}
});
test('drafts, files or active work block refresh and preserve the old page until the user is ready',async()=>{
  const doc=fixture(),win=fixture();let offered,blocked=true,reloads=0,message='';
  const api=setupClientUpdates({version:'a'.repeat(64),request:async()=>response('b'.repeat(64)),show:fn=>offered=fn,refresh:async()=>reloads++,blocked:()=>blocked,toast:v=>message=v,document:doc,window:win});
  try{await api.check();await offered();assert.equal(reloads,0);assert.match(message,/черновик/);blocked=false;await offered();assert.equal(reloads,1);}finally{api.stop();}
});
test('same versions, offline failures and old endpoints do not interrupt chat; cleanup removes listeners',async()=>{
  const doc=fixture(),win=fixture();let mode=0,shown=0;
  const api=setupClientUpdates({version:'a'.repeat(64),request:async()=>{if(mode===1)throw Error('offline');if(mode===2)return {ok:false};return response('a'.repeat(64));},show:()=>shown++,refresh:async()=>{throw Error('must not reload');},document:doc,window:win});
  await api.check();mode=1;await api.check();mode=2;await api.check();assert.equal(shown,0);assert.equal(api.stale(),false);api.stop();assert.equal(doc.listeners.size,0);assert.equal(win.listeners.size,0);
});
