import type { Hono } from 'hono';
import { integerText } from '../billing/integers.js';
import { eventBatchSize,type EventStore,type JobEvent } from '../db/events.js';
import type { JobStore } from '../db/jobs.js';
import { ApiError,type ApiEnv } from '../types.js';
import { getOnly } from './get-only.js';
import { rawInteger } from './job-request.js';

export interface EventServices {
  jobs:Pick<JobStore,'getOwned'>;
  events:Pick<EventStore,'readEvents'|'readStatus'>;
}
const terminal=new Set(['succeeded','failed','cancelled']);

function frame(event:JobEvent):Uint8Array {
  const progress=JSON.parse(event.progressJson,(_key:string,value:unknown,context?:{source?:string})=>
    typeof value==='number'&&context?.source&&/^-?\d+$/.test(context.source)?rawInteger(context.source):value);
  const payload=JSON.stringify({sequence:rawInteger(event.sequence),type:event.type,
    progress:{stage:progress.stage,completed:progress.completed,total:progress.total}});
  return new TextEncoder().encode(`id: ${event.sequence}\nevent: ${event.type}\ndata: ${payload}\n\n`);
}

function wait(milliseconds:number,signal:AbortSignal):Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve,reject)=>{
    const done=()=>{signal.removeEventListener('abort',abort);resolve();};
    const timer=setTimeout(done,milliseconds);
    const abort=()=>{clearTimeout(timer);signal.removeEventListener('abort',abort);reject(signal.reason);};
    signal.addEventListener('abort',abort,{once:true});
  });
}

export function registerEventRoutes(app:Hono<ApiEnv>,services:EventServices):void {
  app.get('/v1/jobs/:id/events',getOnly,async c=>{
    const id=c.req.param('id');
    // Ownership is checked before constructing a stream or starting event polling.
    await services.jobs.getOwned(id,c.get('principal').userId,c.req.raw.signal);
    const parsed=integerText(c.req.header('Last-Event-ID')??'0');
    let cursor=parsed!==null&&parsed>0n?parsed:0n;
    const lifetime=new AbortController(),signal=lifetime.signal;
    let stopped=false,buffer:JobEvent[]=[],needsStatus=false,terminalObserved=false,delay=1000;
    let controller:ReadableStreamDefaultController<Uint8Array>;
    const stop=()=>{
      if(stopped)return;
      stopped=true;buffer=[];lifetime.abort();c.req.raw.signal.removeEventListener('abort',disconnect);
    };
    const disconnect=()=>{stop();controller.close();};
    const body=new ReadableStream<Uint8Array>({
      start(value){controller=value;c.req.raw.signal.addEventListener('abort',disconnect,{once:true});if(c.req.raw.signal.aborted)disconnect();},
      async pull(value){
        try {
          while(!stopped){
            const next=buffer.shift();
            if(next){cursor=next.sequence;value.enqueue(frame(next));return;}
            if(needsStatus){
              needsStatus=false;
              terminalObserved=terminal.has(await services.events.readStatus(id,signal));
              if(!terminalObserved){await wait(delay,signal);delay=Math.min(delay*2,5000);}
            }
            if(stopped)return;
            const rows=await services.events.readEvents(id,cursor,eventBatchSize,signal);
            if(stopped)return;
            if(rows.length){buffer=rows;delay=1000;needsStatus=rows.length<eventBatchSize&&!terminalObserved;continue;}
            // Status and event writes commit atomically, but may commit between reads.
            // After observing terminal, drain a fresh keyset read before closing.
            if(terminalObserved){stop();value.close();return;}
            needsStatus=true;
          }
        }catch(error){if(!stopped){stop();value.error(error);}}
      },
      cancel(){stop();},
    },{highWaterMark:0});
    return new Response(body,{headers:{'Content-Type':'text/event-stream; charset=utf-8'}});
  });
  app.all('/v1/jobs/:id/events',()=>{throw new ApiError(405,'method_not_allowed','Method not allowed');});
}
