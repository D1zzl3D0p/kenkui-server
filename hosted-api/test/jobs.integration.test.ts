import { execFileSync,spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { Client,Pool } from 'pg';
import { afterEach,beforeEach,describe,expect,it } from 'vitest';
import { JobStore } from '../src/db/jobs.js';
import { NotificationStore } from '../src/db/notifications.js';
import { jobSpec } from '../src/billing/job-spec.js';
import { jobResponse,registerJobRoutes } from '../src/routes/jobs.js';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { AssetStore } from '../src/db/assets.js';
import { BillingStore } from '../src/db/billing.js';
const connectionString=process.env.KENKUI_TEST_DATABASE_URL;
const source='00000000-0000-0000-0000-000000000001',owner='00000000-0000-0000-0000-000000000002',account='00000000-0000-0000-0000-000000000003';
const spec=jobSpec({sourceId:source,chapters:['chapter'],casting:{voiceId:'narrator'}});
describe.skipIf(!connectionString)('hosted job lifecycle on real PostgreSQL',()=>{
  let schema:string,admin:Client,pool:Pool,jobs:JobStore;
  const python=(op:string,...args:string[])=>execFileSync('../.venv/bin/python',['test/helpers/billing_database.py',schema,op,...args],{encoding:'utf8'});
  const oracle=(op:string,...args:string[])=>execFileSync('../.venv/bin/python',['test/helpers/jobs_database.py',schema,op,...args],{encoding:'utf8'});
  const admit=()=>jobs.admitJob({jobId:randomUUID(),dispatchId:randomUUID(),ownerId:owner,accountId:account,spec,credits:7n,idempotencyKey:null});
  beforeEach(async()=>{
    expect(connectionString).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
    schema='billing_test_'+randomUUID().replaceAll('-','');admin=new Client({connectionString,ssl:false});await admin.connect();await admin.query('CREATE SCHEMA '+schema);python('init');
    pool=new Pool({connectionString,ssl:false,options:'-c search_path='+schema,application_name:schema,max:5});jobs=new JobStore(()=>pool);
  });
  afterEach(async()=>{await pool?.end();if(admin){await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.end();}});
  it('keeps owner-only sorted snapshots, failures, raw large progress, and exact completed Python responses',async()=>{
    const job=await admit();
    expect((await jobs.listOwned(owner)).map(x=>x.jobId)).toEqual([job.jobId]);expect(await jobs.listOwned(randomUUID())).toEqual([]);
    await expect(jobs.getOwned(job.jobId,randomUUID())).rejects.toMatchObject({status:403});
    await expect(jobs.getOwned('missing',owner)).rejects.toMatchObject({status:404});
    await pool.query("UPDATE jobs SET progress_json=$1",['{"stage":"running","completed":9007199254740993,"total":9007199254740995}']);
    await pool.query("INSERT INTO job_failures VALUES($1,'worker_lost','safe message')",[job.jobId]);
    expect(JSON.stringify(jobResponse(await jobs.getOwned(job.jobId,owner),true))).toContain('"completed":9007199254740993,"total":9007199254740995');
    expect(jobResponse(await jobs.getOwned(job.jobId,owner),true)).toMatchObject({failure:{code:'worker_lost',message:'safe message'}});
    await pool.query('DELETE FROM job_failures');python('settle',job.jobId);
    expect(JSON.parse(JSON.stringify(jobResponse(await jobs.getOwned(job.jobId,owner),true)))).toEqual(JSON.parse(oracle('response',job.jobId)));
    const path=await jobs.artifactForJob(job.jobId);expect(path).toMatch(new RegExp('^'+job.jobId+'\\.[a-f0-9-]{36}$'));
    expect((await pool.query('SELECT status FROM credit_authorizations')).rows).toEqual([{status:'settled'}]);
    expect((await pool.query('SELECT count(*) FROM execution_leases')).rows[0].count).toBe('1');
  });
  it('runs real HTTP admission through committed TS rows into the fenced Python worker, then returns the durable owned response',async()=>{
    const config=loadConfig({KENKUI_ENV:'staging'});
    config.catalog.voices=[{id:'narrator',name:'Fixture',language:null,licenseId:null,voiceRights:null}];
    const app=createApi({config,getPool:()=>pool,auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
    const assertOutsideTransaction=async()=>expect((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND state='idle in transaction'",[schema])).rows[0].count).toBe('0');
    const operations:string[]=[];
    registerJobRoutes(app,config,()=>({
      jobs,assets:new AssetStore(()=>pool),billing:new BillingStore(()=>pool),
      signals:{async arm(){await assertOutsideTransaction();operations.push('arm');},async clear(){await assertOutsideTransaction();operations.push('clear');}},
      objects:{async artifactUrl(){throw Error('unexpected object transfer');}},
      bridge:{
        async preflight(_owner,job){await assertOutsideTransaction();expect(job).toEqual({sourceId:source,chapters:['chapter'],casting:{voiceId:'narrator'},tts:{prepareNumbers:true}});return {sourceId:source,normalizedCharacters:25000,valid:true,availableCredits:null,estimatedCredits:null};},
        async dispatch(dispatchId){
          await assertOutsideTransaction();operations.push('dispatch');
          const row=(await pool.query('SELECT job_id FROM dispatches WHERE id=$1',[dispatchId])).rows[0];
          expect(row).toBeDefined();python('settle',row.job_id);return {accepted:true};
        },
      },
    }));
    const headers={Authorization:'Bearer fixture','Content-Type':'application/json','Idempotency-Key':'http'};
    const request={sourceId:source,chapters:['chapter'],casting:{voiceId:'narrator'},tts:{prepareNumbers:true},normalizedCharacters:1};
    const admitted=await app.request('/v1/jobs',{method:'POST',headers,body:JSON.stringify(request)});expect(admitted.status).toBe(202);
    const body=await admitted.json(),read=await app.request('/v1/jobs/'+body.id,{headers});
    expect(await read.json()).toEqual(JSON.parse(oracle('response',body.id)));
    const replay=await app.request('/v1/jobs',{method:'POST',headers,body:JSON.stringify(request)});
    expect(replay.status).toBe(202);expect((await replay.json()).status).toBe('succeeded');
    expect(operations).toEqual(['arm','dispatch','arm','arm','clear']);
    expect((await pool.query('SELECT count(*) FROM credit_authorizations')).rows[0].count).toBe('1');
    expect((await pool.query("SELECT status FROM credit_authorizations")).rows[0].status).toBe('settled');
    expect((await pool.query('SELECT attempts FROM execution_leases')).rows[0].attempts).toBe(1);
  });
  it('ports exact queued cancellation release/events and prevents later worker claim',async()=>{
    const candidate=await admit(),before=JSON.parse(python('snapshot'));
    await expect(jobs.cancelOwned(candidate.jobId,randomUUID())).rejects.toMatchObject({status:403});
    const cancelled=await jobs.cancelOwned(candidate.jobId,owner);expect(cancelled.status).toBe('cancelled');
    const result=JSON.parse(python('snapshot'));expect(await jobs.cancelOwned(candidate.jobId,owner)).toEqual(cancelled);expect(JSON.parse(python('snapshot'))).toEqual(result);
    expect(oracle('claim',candidate.dispatchId).trim()).toBe('null');
    python('reset');python('admit',candidate.jobId,candidate.dispatchId); // Python uses the same intent and 7 credits.
    const original=JSON.parse(python('snapshot'));delete before.job_idempotency;delete original.job_idempotency;expect(original).toEqual(before);
    python('release',candidate.jobId);const expected=JSON.parse(python('snapshot'));delete result.job_idempotency;delete expected.job_idempotency;expect(result).toEqual(expected);
  });
  it('canonicalizes historical cancellation progress exactly like Python without rounding large integers',async()=>{
    const job=await admit(),progress='{ "total":9007199254740995, "stage":"préparing 😀", "completed":9007199254740993 }';
    await pool.query('UPDATE jobs SET progress_json=$1',[progress]);await jobs.cancelOwned(job.jobId,owner);
    const actual=JSON.parse(python('snapshot'));
    python('reset');python('admit',job.jobId,job.dispatchId);await pool.query('UPDATE jobs SET progress_json=$1',[progress]);python('release',job.jobId);
    const expected=JSON.parse(python('snapshot'));delete actual.job_idempotency;delete expected.job_idempotency;
    expect(actual).toEqual(expected);
    expect(JSON.stringify(jobResponse(await jobs.getOwned(job.jobId,owner)))).toContain('"completed":9007199254740993');
  });
  it('rolls the entire cancellation back on a lot mismatch without replaying any substep',async()=>{
    const job=await admit();await pool.query('UPDATE credit_allocations SET credits=credits+1 WHERE lot_id=(SELECT lot_id FROM credit_allocations LIMIT 1)');
    const snapshot=python('snapshot');await expect(jobs.cancelOwned(job.jobId,owner)).rejects.toThrow('credit_lot_balance_mismatch');expect(python('snapshot')).toBe(snapshot);
  });
  it('retains preference email/internal identity and Python completion notification claims',async()=>{
    const job=await admit(),notifications=new NotificationStore(()=>pool);
    await pool.query('UPDATE identities SET email=$1,notify_by_email=true WHERE id=$2',['stored@example.invalid',owner]);
    expect(await notifications.settings(owner)).toEqual({email:'stored@example.invalid',emailOnCompletion:true});
    await notifications.setEnabled(owner,false);expect(oracle('preferences',job.jobId).trim()).toBe('stored@example.invalid|False|True|False');
    await notifications.setEnabled(owner,true);expect(await notifications.settings(owner)).toEqual({email:'stored@example.invalid',emailOnCompletion:true});
    expect((await pool.query('SELECT count(*) FROM job_notifications')).rows[0].count).toBe('1');
  });
  it.each(['cancel','complete'])('makes cancellation versus Python completion durable when %s wins the row lock',async winner=>{
    const job=await admit();await pool.query("UPDATE jobs SET status='running',version=1");await pool.query("UPDATE dispatches SET status='running',version=1");
    const lock=await pool.connect();await lock.query('BEGIN');await lock.query('SELECT id FROM jobs WHERE id=$1 FOR UPDATE',[job.jobId]);
    let child:ReturnType<typeof spawn>|undefined;
    const startWorker=()=>{
      const worker=spawn('../.venv/bin/python',['test/helpers/jobs_database.py',schema,'complete',job.jobId],{env:{...process.env},stdio:['ignore','pipe','pipe']});
      child=worker;
      let output='',errors='';worker.stdout.on('data',x=>output+=String(x));worker.stderr.on('data',x=>errors+=String(x));
      return new Promise<string>((resolve,reject)=>{worker.on('exit',code=>code===0?resolve(output.trim()):reject(Error(errors)));worker.on('error',reject);});
    };
    const waitForLocks=async(n:number)=>{
      const until=Date.now()+4000;let count=0;
      while(Date.now()<until){count=Number((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE (application_name=$1 OR application_name=$2) AND wait_event_type='Lock'",[schema,schema+'_python'])).rows[0].count);if(count>=n)return;await new Promise(r=>setTimeout(r,10));}
      throw Error('Expected '+n+' blocked lifecycle operations; saw '+count);
    };
    let cancellation:Promise<unknown>,completion:Promise<string>;
    try{
      if(winner==='complete'){
        completion=startWorker();await waitForLocks(1);
        cancellation=jobs.cancelOwned(job.jobId,owner).catch(e=>e);
      }else{
        cancellation=jobs.cancelOwned(job.jobId,owner).catch(e=>e);await waitForLocks(1);
        completion=startWorker();
      }
      await waitForLocks(2);
    }finally{await lock.query('ROLLBACK');lock.release();}
    const [cancelResult,completeResult]=await Promise.all([cancellation!,completion!]);
    if(winner==='complete'){expect(completeResult).toBe('succeeded');expect(cancelResult).toMatchObject({message:'stale_write'});}
    else{
      expect(completeResult).toBe('stale_write');
      expect((await jobs.getOwned(job.jobId,owner)).status).toBe('cancel_requested');
      expect(oracle('complete',job.jobId).trim()).toBe('cancelled');
    }
    const snapshot=await jobs.getOwned(job.jobId,owner);
    expect(snapshot.status).toBe(winner==='complete'?'succeeded':'cancelled');
    const authorization=(await pool.query('SELECT status FROM credit_authorizations')).rows[0].status;
    expect(authorization).toBe(winner==='complete'?'settled':'released');
    expect((await pool.query("SELECT count(*) FROM credit_ledger_entries WHERE kind IN ('release','settlement')")).rows[0].count).toBe('1');
  });
});
