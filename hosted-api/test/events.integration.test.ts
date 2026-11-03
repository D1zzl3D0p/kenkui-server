import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { Client,Pool } from 'pg';
import { afterEach,beforeEach,describe,expect,it,vi } from 'vitest';
import { EventStore,eventQuery } from '../src/db/events.js';
import { JobStore } from '../src/db/jobs.js';
import { registerEventRoutes } from '../src/routes/events.js';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';

const connectionString=process.env.KENKUI_TEST_DATABASE_URL;
const owner='00000000-0000-0000-0000-000000000002';
const headers={Authorization:'Bearer fixture'};
async function until(check:()=>Promise<boolean>){
  const deadline=Date.now()+4000;
  while(Date.now()<deadline){if(await check())return;await new Promise<void>(resolve=>setImmediate(resolve));}
  throw Error('Timed out observing PostgreSQL state');
}
describe.skipIf(!connectionString)('bounded event reads on actual Python-migrated PostgreSQL',()=>{
  let schema:string,admin:Client,pool:Pool,events:EventStore,id:string;
  beforeEach(async()=>{
    expect(connectionString).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
    schema='billing_test_'+randomUUID().replaceAll('-','');admin=new Client({connectionString,ssl:false});await admin.connect();await admin.query('CREATE SCHEMA '+schema);
    const python=(op:string,...args:string[])=>execFileSync('../.venv/bin/python',['test/helpers/billing_database.py',schema,op,...args],{encoding:'utf8'});
    python('init');id=randomUUID();python('admit',id,randomUUID());
    pool=new Pool({connectionString,ssl:false,options:'-c search_path='+schema,application_name:schema,max:1});events=new EventStore(()=>pool);
  });
  afterEach(async()=>{await admin?.query('ROLLBACK');await pool?.end();if(admin){await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.end();}});

  it('uses the existing composite index for bounded keyset reads without scanning delivered history',async()=>{
    await pool.query('DELETE FROM job_events');
    await pool.query("INSERT INTO job_events SELECT $1,n,'progress','{\"stage\":\"rendering\",\"completed\":9007199254740993,\"total\":9007199254740995}' FROM generate_series(1,10000)n",[id]);
    await pool.query('ANALYZE job_events');
    const rows=await events.readEvents(id,9900n,100);expect(rows.map(x=>x.sequence)).toEqual(Array.from({length:100},(_,i)=>BigInt(i+9901)));
    expect(rows[0]!.progressJson).toContain('9007199254740993');
    const plan=(await pool.query('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+eventQuery,[id,'9900',100])).rows[0]['QUERY PLAN'][0].Plan;
    expect(plan['Node Type']).toBe('Limit');expect(plan['Actual Rows']).toBe(100);
    const scan=plan.Plans[0];expect(scan['Node Type']).toBe('Index Scan');expect(scan['Index Name']).toBe('job_events_pkey');
    expect(scan['Index Cond']).toContain('job_id');expect(scan['Index Cond']).toContain('sequence > 9900');expect(scan['Actual Rows']).toBe(100);expect(scan['Rows Removed by Filter']??0).toBe(0);
    for(const cursor of [2147483647n,2147483648n,9007199254740993n,BigInt('9'.repeat(4300))])expect(await events.readEvents(id,cursor,100)).toEqual([]);
    expect((await events.readEvents(id,-BigInt('9'.repeat(4300)),100))[0]!.sequence).toBe(1n);
    await expect(events.readEvents(id,0n,101)).rejects.toThrow('batch size');
    // No checkout or transaction remains while a viewer waits for more data.
    expect(pool.idleCount).toBe(1);expect((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND state='idle in transaction'",[schema])).rows[0].count).toBe('0');
  });

  it('streams a terminal multi-page backlog and reconnects with exact ordered IDs using actual SQL',async()=>{
    await pool.query('DELETE FROM job_events');
    await pool.query("INSERT INTO job_events SELECT $1,n,CASE WHEN n=251 THEN 'succeeded' ELSE 'progress' END,'{\"stage\":\"complete\",\"completed\":9007199254740993,\"total\":9007199254740995}' FROM generate_series(1,251)n",[id]);
    await pool.query("UPDATE jobs SET status='succeeded' WHERE id=$1",[id]);
    const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>pool,auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
    registerEventRoutes(app,{jobs:new JobStore(()=>pool),events});const reads=vi.spyOn(events,'readEvents');
    const first=await app.request('/v1/jobs/'+id+'/events',{headers}),reader=first.body!.getReader();
    for(let n=1;n<=101;n++)expect(new TextDecoder().decode((await reader.read()).value)).toContain('id: '+n+'\n');
    await reader.cancel();expect(reads.mock.calls.map(c=>[c[1],c[2]])).toEqual([[0n,100],[100n,100]]);
    const resumed=await app.request('/v1/jobs/'+id+'/events',{headers:{...headers,'Last-Event-ID':'101'}}),body=await resumed.text();
    expect([...body.matchAll(/^id: (\d+)$/gm)].map(x=>Number(x[1]))).toEqual(Array.from({length:150},(_,i)=>i+102));
    expect(body).toContain('"completed":9007199254740993,"total":9007199254740995');expect(body).toContain('event: succeeded\n');
    expect(reads.mock.calls.map(c=>[c[1],c[2]])).toEqual([[0n,100],[100n,100],[101n,100],[201n,100],[251n,100]]);
  });

  it('keeps real missing/foreign ownership errors before all stream reads',async()=>{
    const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>pool,auth:{async verify(){return {userId:randomUUID(),issuer:'fixture',subject:'fixture'};}}});
    registerEventRoutes(app,{jobs:new JobStore(()=>pool),events});const reads=vi.spyOn(events,'readEvents');
    const foreign=await app.request('/v1/jobs/'+id+'/events',{headers});expect(foreign.status).toBe(403);expect((await foreign.json()).error.message).toBe('resource is not owned by this user');
    expect((await app.request('/v1/jobs/missing/events',{headers})).status).toBe(404);expect(reads).not.toHaveBeenCalled();expect(pool.idleCount).toBe(1);
  });

  it('destroys a blocked event query on stream cancellation, discards late results and releases resources',async()=>{
    const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>pool,auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
    registerEventRoutes(app,{jobs:new JobStore(()=>pool),events});
    const res=await app.request('/v1/jobs/'+id+'/events',{headers});
    await admin.query('BEGIN');await admin.query('LOCK TABLE '+schema+'.job_events IN ACCESS EXCLUSIVE MODE');
    const reader=res.body!.getReader(),pending=reader.read();
    await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');return Number((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock'",[schema])).rows[0].count)===1;});
    await reader.cancel();expect(await pending).toMatchObject({done:true});
    await until(async()=>pool.totalCount===0);
    await admin.query('ROLLBACK');
    expect((await events.readEvents(id,0n,100)).length).toBe(0);expect(pool.idleCount).toBe(1);
  });

  it('aborts a blocked owned lookup before constructing the polling stream',async()=>{
    const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>pool,auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
    registerEventRoutes(app,{jobs:new JobStore(()=>pool),events});const reads=vi.spyOn(events,'readEvents');
    await admin.query('BEGIN');await admin.query('LOCK TABLE '+schema+'.jobs IN ACCESS EXCLUSIVE MODE');
    const abort=new AbortController(),pending=app.request('/v1/jobs/'+id+'/events',{headers,signal:abort.signal});let pid:number|undefined;
    await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');pid=(await admin.query("SELECT pid FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock'",[schema])).rows[0]?.pid;return pid!==undefined;});
    abort.abort();await pending;
    await until(async()=>pool.totalCount===0);expect(reads).not.toHaveBeenCalled();
    await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');return (await admin.query('SELECT pid FROM pg_stat_activity WHERE pid=$1',[pid])).rows.length===0;});
    expect((await admin.query('SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=$1::regclass AND granted',[schema+'.jobs'])).rows).toContainEqual({mode:'AccessExclusiveLock'});
    await admin.query('ROLLBACK');
  },10000);

  it('stops every blocked backend from repeated aborted reconnects while the blocker stays held',async()=>{
    await admin.query('BEGIN');await admin.query('LOCK TABLE '+schema+'.job_events IN ACCESS EXCLUSIVE MODE');
    const pids:number[]=[],pending:Promise<unknown>[]=[];
    try {
      for(let attempt=0;attempt<3;attempt++){
        const abort=new AbortController(),read=events.readEvents(id,0n,100,abort.signal);pending.push(read);
        const rejected=expect(read).rejects.toMatchObject({name:'AbortError'});
        await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');const pid=(await admin.query("SELECT pid FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock' AND NOT(pid=ANY($2::int[]))",[schema,pids])).rows[0]?.pid;if(pid!==undefined){pids.push(pid);return true;}return false;});
        abort.abort();await rejected;expect(pool.totalCount).toBe(0);
      }
      // Pool counters cannot prove PostgreSQL stopped executing a destroyed socket.
      await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');return (await admin.query('SELECT pid FROM pg_stat_activity WHERE pid=ANY($1::int[])',[pids])).rows.length===0;});
      expect((await admin.query('SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=$1::regclass AND granted',[schema+'.job_events'])).rows).toContainEqual({mode:'AccessExclusiveLock'});
    }finally{await admin.query('ROLLBACK');await Promise.allSettled(pending);}
  },10000);

  it('enforces a server statement deadline without client abort and restores settings for normal pool users',async()=>{
    const previous=(await pool.query("SELECT current_setting('statement_timeout') AS statement,current_setting('idle_in_transaction_session_timeout') AS idle")).rows[0];
    await pool.query("SET statement_timeout='8s'; SET idle_in_transaction_session_timeout='12s'");
    expect(await events.readEvents(id,0n,100)).toEqual([]);
    const settings=(await pool.query("SELECT current_setting('statement_timeout') AS statement,current_setting('idle_in_transaction_session_timeout') AS idle,current_setting('transaction_read_only') AS readonly")).rows[0];
    expect(settings).toEqual({statement:'8s',idle:'12s',readonly:'off'});
    await admin.query('BEGIN');await admin.query('LOCK TABLE '+schema+'.job_events IN ACCESS EXCLUSIVE MODE');
    const read=events.readEvents(id,0n,100),rejected=expect(read).rejects.toMatchObject({code:'57014'});let pid:number|undefined;
    try {
      await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');pid=(await admin.query("SELECT pid FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock'",[schema])).rows[0]?.pid;return pid!==undefined;});
      await rejected;
      await until(async()=>{await admin.query('SELECT pg_stat_clear_snapshot()');return (await admin.query('SELECT pid FROM pg_stat_activity WHERE pid=$1',[pid])).rows.length===0;});
      expect((await admin.query('SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation=$1::regclass AND granted',[schema+'.job_events'])).rows).toContainEqual({mode:'AccessExclusiveLock'});
    }finally{await admin.query('ROLLBACK');await read.catch(()=>{});}
    const next=(await pool.query("SELECT current_setting('statement_timeout') AS statement,current_setting('idle_in_transaction_session_timeout') AS idle")).rows[0];expect(next).toEqual(previous);
    expect((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND state LIKE 'idle in transaction%'",[schema])).rows[0].count).toBe('0');
  },10000);

  it('releases a late pool checkout after abort and never runs the event query',async()=>{
    const held=await pool.connect(),abort=new AbortController();
    const pending=events.readEvents(id,0n,100,abort.signal);expect(pool.waitingCount).toBe(1);abort.abort();await expect(pending).rejects.toMatchObject({name:'AbortError'});
    held.release();await until(async()=>pool.totalCount===0&&pool.waitingCount===0);
    expect(await events.readEvents(id,0n,100)).toEqual([]);
  });
});
