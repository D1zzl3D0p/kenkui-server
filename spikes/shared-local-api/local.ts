/** Throwaway adapter experiment: retains Python state authority; never released. */
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { Hono } from '../../hosted-api/node_modules/hono/dist/index.js';

const here=dirname(fileURLToPath(import.meta.url));
export function localExperiment(python:string,state:string,source:string){
  const child=spawn(python,[resolve(here,'worker.py'),'--state',state,'--source',source],{stdio:['pipe','pipe','inherit']});
  let closed=false;
  const pending=new Map<string,{resolve:(x:any)=>void;reject:(e:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
  let readyResolve:()=>void,readyReject:(e:Error)=>void;
  const ready=new Promise<void>((ok,fail)=>{readyResolve=ok;readyReject=fail;});
  const startup=setTimeout(()=>child.kill(),15000);
  const lines=createInterface({input:child.stdout});
  lines.on('line',line=>{
    if(Buffer.byteLength(line)>1048576){child.kill();return;}
    try{
      const message=JSON.parse(line);
      if(message.type==='ready'&&message.protocolVersion===1){clearTimeout(startup);readyResolve();return;}
      const request=pending.get(message.requestId);
      if(request){clearTimeout(request.timer);pending.delete(message.requestId);request.resolve(message);}
    }catch{child.kill();}
  });
  const fail=()=>{closed=true;clearTimeout(startup);readyReject(new Error('Worker exited'));for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error('Worker exited'));}pending.clear();};
  child.once('error',fail);child.once('exit',fail);
  async function call(operation:string,input:unknown={}){
    await ready;if(closed)throw Error('Worker exited');
    const requestId=randomUUID(),frame=JSON.stringify({protocolVersion:1,requestId,operation,input})+'\n';
    if(Buffer.byteLength(frame)>1048576)throw Error('Frame exceeds limit');
    return new Promise<any>((ok,reject)=>{
      const timer=setTimeout(()=>{pending.delete(requestId);reject(Error('Operation timeout; reconcile durable state'));},15000);
      pending.set(requestId,{resolve:ok,reject,timer});child.stdin.write(frame);
    });
  }
  const app=new Hono();
  // This is deliberately an evaluation of the existing Python-state adapter.
  // It demonstrates the duplication if we preserve its full public API behind RPC.
  app.post('/v1/assets',async c=>{const r=await call('upload');return c.json(r.result,r.status);});
  app.get('/v1/assets/:id/book',async c=>{const r=await call('inspect',{sourceId:c.req.param('id')});return c.json(r.result,r.status);});
  for(const [path,operation] of [['/v1/jobs/preflight','preflight'],['/v1/jobs','start']] as const)
    app.post(path,async c=>{const r=await call(operation,await c.req.json());return c.json(r.result,r.status);});
  app.get('/v1/jobs/:id',async c=>{const r=await call('snapshot',{jobId:c.req.param('id')});return c.json(r.result,r.status);});
  app.post('/v1/jobs/:id/cancel',async c=>{const r=await call('cancel',{jobId:c.req.param('id')});return c.json(r.result,r.status);});
  return {app,call,ready,kill:()=>child.kill('SIGKILL'),async close(){
    if(closed)return;
    await new Promise<void>(done=>{const timer=setTimeout(()=>child.kill('SIGKILL'),15000);child.once('exit',()=>{clearTimeout(timer);done();});child.stdin.end();});
  }};
}
