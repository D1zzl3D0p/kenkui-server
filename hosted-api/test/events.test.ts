import { afterEach,expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { ApiError } from '../src/types.js';
import { jobSpec } from '../src/billing/job-spec.js';
import { registerEventRoutes } from '../src/routes/events.js';
import { streamQuery } from '../src/db/stream-query.js';
import type { Pool,PoolClient } from 'pg';

const owner='00000000-0000-0000-0000-000000000002',id='contract-job';
const headers={Authorization:'Bearer fixture','X-Request-ID':'contract-request'};
const event=(sequence:bigint,type='running')=>({sequence,type,progressJson:'{"stage":"complete","completed":1,"total":1}'});
function setup(){
  const config=loadConfig({KENKUI_ENV:'staging',KENKUI_ALLOWED_ORIGINS:'https://studio.example.invalid'});
  const jobs={getOwned:vi.fn(async()=>({jobId:id,spec:jobSpec({sourceId:id,chapters:['one'],casting:{voiceId:'narrator'}}),status:'running',version:0,progressJson:'{}'}))};
  const events={readEvents:vi.fn(async(_id:string,_after:bigint,_limit:number,_signal?:AbortSignal)=>[] as ReturnType<typeof event>[]),readStatus:vi.fn(async()=> 'running')};
  const app=createApi({config,getPool:()=>{throw Error('unexpected DB');},auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
  registerEventRoutes(app,{jobs,events});
  return {app,jobs,events};
}
const decode=(value:Uint8Array|undefined)=>new TextDecoder().decode(value);
afterEach(()=>vi.useRealTimers());

it('preserves the frozen resume bytes, headers and terminal snapshot behavior',async()=>{
  const s=setup();s.events.readEvents.mockImplementation(async(_id,after)=>after<2n?[event(2n,'succeeded')]:[]);s.events.readStatus.mockResolvedValue('succeeded');
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers:{...headers,'Last-Event-ID':'1'}});
  expect(res.status).toBe(200);expect(res.headers.get('content-type')).toBe('text/event-stream; charset=utf-8');expect(res.headers.get('x-request-id')).toBe('contract-request');
  expect(await res.text()).toBe('id: 2\nevent: succeeded\ndata: {"sequence":2,"type":"succeeded","progress":{"stage":"complete","completed":1,"total":1}}\n\n');
  expect(s.jobs.getOwned).toHaveBeenCalledWith(id,owner,expect.any(AbortSignal));
  expect(s.events.readEvents.mock.calls.map(c=>c[1])).toEqual([1n,2n]);
});

it('starts with a 1 second wait, backs off to 5 seconds and resets after new events',async()=>{
  vi.useFakeTimers();const s=setup(),abort=new AbortController();
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers,signal:abort.signal});const reader=res.body!.getReader();
  const pending=reader.read();await vi.advanceTimersByTimeAsync(0);expect(s.events.readEvents).toHaveBeenCalledTimes(1);
  for(const [delay,calls] of [[1000,2],[2000,3],[4000,4],[5000,5],[5000,6]]){
    await vi.advanceTimersByTimeAsync(delay!-1);expect(s.events.readEvents).toHaveBeenCalledTimes(calls!-1);
    await vi.advanceTimersByTimeAsync(1);expect(s.events.readEvents).toHaveBeenCalledTimes(calls!);
  }
  s.events.readEvents.mockResolvedValueOnce([event(7n)]);
  await vi.advanceTimersByTimeAsync(5000);expect(decode((await pending).value)).toContain('id: 7\n');
  const next=reader.read();await vi.advanceTimersByTimeAsync(0);const count=s.events.readEvents.mock.calls.length;
  await vi.advanceTimersByTimeAsync(999);expect(s.events.readEvents).toHaveBeenCalledTimes(count);
  await vi.advanceTimersByTimeAsync(1);expect(s.events.readEvents).toHaveBeenCalledTimes(count+1);
  abort.abort();expect(await next).toMatchObject({done:true});expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(30000);expect(s.events.readEvents).toHaveBeenCalledTimes(count+1);
});

it('drains multiple bounded pages before terminal closure and resumes in order',async()=>{
  const s=setup(),history=Array.from({length:251},(_,i)=>event(BigInt(i+1),i===250?'succeeded':'progress'));
  s.events.readEvents.mockImplementation(async(_id,after,limit)=>history.filter(e=>e.sequence>after).slice(0,limit));s.events.readStatus.mockResolvedValue('succeeded');
  const first=await s.app.request('/v1/jobs/'+id+'/events',{headers});const reader=first.body!.getReader();
  expect(s.events.readEvents).not.toHaveBeenCalled();
  for(let i=1;i<=101;i++)expect(decode((await reader.read()).value)).toContain('id: '+i+'\n');
  expect(s.events.readEvents).toHaveBeenCalledTimes(2);await reader.cancel();
  const resumed=await s.app.request('/v1/jobs/'+id+'/events',{headers:{...headers,'Last-Event-ID':'101'}});const body=await resumed.text();
  expect([...body.matchAll(/^id: (\d+)$/gm)].map(x=>BigInt(x[1]!))).toEqual(history.slice(101).map(x=>x.sequence));
  expect(s.events.readEvents.mock.calls.map(c=>[c[1],c[2]])).toEqual([[0n,100],[100n,100],[101n,100],[201n,100],[251n,100]]);
});

it('rechecks history after terminal status to retain an event committed between the two reads',async()=>{
  const s=setup();s.events.readEvents.mockResolvedValueOnce([]).mockResolvedValueOnce([event(1n,'failed')]).mockResolvedValue([]);s.events.readStatus.mockResolvedValue('failed');
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers});expect(await res.text()).toContain('id: 1\nevent: failed\n');
});

it.each(['succeeded','failed','cancelled'])('closes %s after all history; cancel_requested stays active',async status=>{
  const s=setup();s.events.readStatus.mockResolvedValue(status);const res=await s.app.request('/v1/jobs/'+id+'/events',{headers});expect(await res.text()).toBe('');
});

it('preserves exact event sequence and stored progress integer tokens above 2^53',async()=>{
  const s=setup();s.events.readEvents.mockResolvedValueOnce([{sequence:9007199254740993n,type:'progress',progressJson:'{"stage":"prép 😀","completed":9007199254740993,"total":9007199254740995,"extra":1}'}]);s.events.readStatus.mockResolvedValue('cancelled');
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers:{...headers,'Last-Event-ID':'9007199254740992'}});
  expect(await res.text()).toBe('id: 9007199254740993\nevent: progress\ndata: {"sequence":9007199254740993,"type":"progress","progress":{"stage":"prép 😀","completed":9007199254740993,"total":9007199254740995}}\n\n');
  expect(s.events.readEvents.mock.calls[0]![1]).toBe(9007199254740992n);
});

it.each([['',0n],['garbage',0n],['1.0',0n],['1e2',0n],['-100',0n],[' +1_2 ',12n],['9'.repeat(4300),BigInt('9'.repeat(4300))],['9'.repeat(4301),0n]])('handles Python Last-Event-ID parsing (%s)',async(value,expected)=>{
  const s=setup();s.events.readStatus.mockResolvedValue('succeeded');const res=await s.app.request('/v1/jobs/'+id+'/events',{headers:{...headers,'Last-Event-ID':value}});await res.text();expect(s.events.readEvents.mock.calls[0]![1]).toBe(expected);
});

it('authorizes before any event query; rejects HEAD/known methods before owner lookup and preserves unknown/OPTIONS',async()=>{
  const s=setup(),path='/v1/jobs/'+id+'/events';
  expect((await s.app.request(path)).status).toBe(401);
  for(const method of ['HEAD','POST','PATCH','OPTIONS'])expect((await s.app.request(path,{method,headers})).status).toBe(405);
  expect((await s.app.request(path+'/unknown',{headers})).status).toBe(404);expect(s.jobs.getOwned).not.toHaveBeenCalled();expect(s.events.readEvents).not.toHaveBeenCalled();
  for(const status of [403,404] as const){s.jobs.getOwned.mockRejectedValueOnce(new ApiError(status,'http_error','fixture'));expect((await s.app.request(path,{headers})).status).toBe(status);}
  expect(s.events.readEvents).not.toHaveBeenCalled();
});

it('cancels idle timers and aborts a pending event query when the consumer disconnects',async()=>{
  vi.useFakeTimers();const s=setup();let signal:AbortSignal|undefined;
  s.events.readEvents.mockImplementationOnce(async(_id,_after,_limit,value)=>{signal=value;return new Promise((resolve,reject)=>{value!.addEventListener('abort',()=>reject(value!.reason),{once:true});});});
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers});const reader=res.body!.getReader(),pending=reader.read();await vi.advanceTimersByTimeAsync(0);
  await reader.cancel();expect(signal!.aborted).toBe(true);expect(await pending).toMatchObject({done:true});expect(vi.getTimerCount()).toBe(0);
});

it.each(['checkout','query'])('bounds a stalled %s to five seconds, handles late rejection and releases exactly once',async phase=>{
  vi.useFakeTimers();let resolveCheckout!:(client:PoolClient)=>void,rejectQuery!:(error:Error)=>void;
  const release=vi.fn(),query=vi.fn(()=>new Promise((_resolve,reject)=>{rejectQuery=reject;}));
  const client={release,query} as unknown as PoolClient;
  const pool={connect:vi.fn(()=>phase==='query'?Promise.resolve(client):new Promise<PoolClient>(resolve=>{resolveCheckout=resolve;}))} as unknown as Pool;
  const pending=streamQuery(()=>pool,'SELECT 1',[]);const rejected=expect(pending).rejects.toThrow('deadline exceeded');
  await vi.advanceTimersByTimeAsync(4999);expect(release).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);await rejected;expect(vi.getTimerCount()).toBe(0);
  if(phase==='checkout'){resolveCheckout(client);await vi.advanceTimersByTimeAsync(0);expect(query).not.toHaveBeenCalled();}
  else {rejectQuery(Error('late socket closure'));await vi.advanceTimersByTimeAsync(0);}
  expect(release).toHaveBeenCalledExactlyOnceWith(true);
});

it.each(['setup','read','commit','rollback'])('handles abort during %s without later SQL, leaked timers or repeated release',async phase=>{
  vi.useFakeTimers();let unblock!:(value:unknown)=>void;
  const release=vi.fn(),query=vi.fn(async(sql:string)=>{
    const step=sql.startsWith('BEGIN')?'setup':sql==='COMMIT'?'commit':sql==='ROLLBACK'?'rollback':'read';
    if(phase==='rollback'&&step==='read')throw Error('read failed');
    if(step===phase)return new Promise(resolve=>{unblock=resolve;});
    return {rows:step==='read'?[{value:'kept'}]:[]};
  });
  const pool={connect:vi.fn(async()=>({query,release}))} as unknown as Pool,abort=new AbortController();
  const pending=streamQuery(()=>pool,'SELECT 1',[],abort.signal),rejected=expect(pending).rejects.toMatchObject({name:'AbortError'});
  await vi.advanceTimersByTimeAsync(0);expect(unblock).toBeTypeOf('function');const count=query.mock.calls.length;
  abort.abort();await rejected;unblock({rows:[]});await vi.advanceTimersByTimeAsync(0);
  expect(query).toHaveBeenCalledTimes(count);expect(release).toHaveBeenCalledExactlyOnceWith(true);expect(vi.getTimerCount()).toBe(0);
});

it('commits before returning rows and rolls back failed reads without leaking transaction settings',async()=>{
  vi.useFakeTimers();const release=vi.fn();let fail=false;
  const query=vi.fn(async(sql:string)=>{if(sql==='SELECT 1'){if(fail)throw Error('read failed');return {rows:[{value:'kept'}]};}return {rows:[]};});
  const pool={connect:vi.fn(async()=>({query,release}))} as unknown as Pool;
  expect(await streamQuery(()=>pool,'SELECT 1',[])).toEqual([{value:'kept'}]);
  expect(query.mock.calls.map(c=>c[0])).toEqual(["BEGIN READ ONLY; SET LOCAL statement_timeout = '3500ms'; SET LOCAL idle_in_transaction_session_timeout = '3500ms'",'SELECT 1','COMMIT']);expect(release).toHaveBeenCalledExactlyOnceWith(false);
  query.mockClear();release.mockClear();fail=true;await expect(streamQuery(()=>pool,'SELECT 1',[])).rejects.toThrow('read failed');
  expect(query.mock.calls.map(c=>c[0])).toEqual(["BEGIN READ ONLY; SET LOCAL statement_timeout = '3500ms'; SET LOCAL idle_in_transaction_session_timeout = '3500ms'",'SELECT 1','ROLLBACK']);expect(release).toHaveBeenCalledExactlyOnceWith(true);expect(vi.getTimerCount()).toBe(0);
});

it('reduces the server timeout after delayed checkout to stay inside the request deadline',async()=>{
  vi.useFakeTimers();let checkout!:(value:unknown)=>void;const release=vi.fn(),query=vi.fn(async()=>({rows:[]}));
  const pool={connect:vi.fn(()=>new Promise(resolve=>{checkout=resolve;}))} as unknown as Pool;
  const pending=streamQuery(()=>pool,'SELECT 1',[]);await vi.advanceTimersByTimeAsync(4000);checkout({query,release});await pending;
  expect(query.mock.calls[0]).toEqual(["BEGIN READ ONLY; SET LOCAL statement_timeout = '500ms'; SET LOCAL idle_in_transaction_session_timeout = '500ms'"]);expect(release).toHaveBeenCalledExactlyOnceWith(false);expect(vi.getTimerCount()).toBe(0);
});

it('does not classify cancel_requested as terminal and closes a request-aborted idle stream',async()=>{
  vi.useFakeTimers();const s=setup();s.events.readStatus.mockResolvedValue('cancel_requested');const abort=new AbortController();
  const res=await s.app.request('/v1/jobs/'+id+'/events',{headers,signal:abort.signal});const reader=res.body!.getReader(),pending=reader.read();await vi.advanceTimersByTimeAsync(5000);
  expect(s.events.readEvents.mock.calls.length).toBeGreaterThan(1);abort.abort();expect(await pending).toMatchObject({done:true});expect(vi.getTimerCount()).toBe(0);
});

it('permits only the two scoped browser preflights while preserving ordinary CORS headers exactly',async()=>{
  const s=setup();const preflight=(path:string,method:string,requestedHeaders:string,origin='https://studio.example.invalid')=>s.app.request(path,{method:'OPTIONS',headers:{Origin:origin,'Access-Control-Request-Method':method,'Access-Control-Request-Headers':requestedHeaders}});
  const ordinary=await preflight('/v1/jobs','POST','Authorization,Content-Type');expect(ordinary.status).toBe(200);expect(await ordinary.text()).toBe('OK');
  expect(ordinary.headers.get('access-control-allow-methods')).toBe('GET, POST, DELETE, OPTIONS');expect(ordinary.headers.get('access-control-allow-headers')).toBe('Accept, Accept-Language, Authorization, Content-Language, Content-Type, Idempotency-Key');
  expect((await preflight('/v1/notifications','PATCH','Authorization,Content-Type')).status).toBe(200);
  expect((await preflight('/v1/jobs/'+id+'/events','GET','Authorization,Last-Event-ID')).status).toBe(200);
  for(const [path,method,requested] of [['/v1/jobs','PATCH','Authorization'],['/v1/notifications/unsubscribe','PATCH','Authorization'],['/v1/jobs','GET','Last-Event-ID'],['/v1/jobs/'+id+'/events/deeper','GET','Last-Event-ID']])expect((await preflight(path!,method!,requested!)).status).toBe(400);
  for(const [path,method,requested] of [['/v1/notifications','PATCH','Authorization,Content-Type'],['/v1/jobs/'+id+'/events','GET','Authorization,Last-Event-ID']]){const res=await preflight(path!,method!,requested!,'https://foreign.invalid');expect(res.status).toBe(400);expect(res.headers.has('access-control-allow-origin')).toBe(false);}
  expect(s.jobs.getOwned).not.toHaveBeenCalled();expect(s.events.readEvents).not.toHaveBeenCalled();
});
