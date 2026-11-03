import { execFileSync } from 'node:child_process';
import { randomUUID,createHmac } from 'node:crypto';
import { StripePayments } from '../src/billing/stripe.js';
import { Client, Pool } from 'pg';
import { beforeEach, afterEach, describe, expect, it } from 'vitest';
import { JobStore, submitJob } from '../src/db/jobs.js';
import { BillingStore } from '../src/db/billing.js';
import { jobSpec, encodeSpec, creditsForCharacters, scopedKey } from '../src/billing/job-spec.js';

const owner='00000000-0000-0000-0000-000000000002', account='00000000-0000-0000-0000-000000000003';
const source='00000000-0000-0000-0000-000000000001', raw={sourceId:source,chapters:[' chapter '],casting:{voiceId:' narrator '}};
const spec=jobSpec(raw), connectionString=process.env.KENKUI_TEST_DATABASE_URL;
const input=(key:string|null=null)=>({jobId:randomUUID(),dispatchId:randomUUID(),ownerId:owner,accountId:account,spec,credits:7n,idempotencyKey:key});
it('matches integer pricing, Python Unicode casting order, and owner-scoped raw keys',()=>{
  expect(creditsForCharacters(3968,false)).toBe(1n);expect(creditsForCharacters(3969,false)).toBe(2n);
  expect(creditsForCharacters(2646,true)).toBe(2n);expect(creditsForCharacters(10n**14n,true)).toBe(37800000000n);
  expect(scopedKey(owner,'e\u0301😀')).toBe('905b4ab4c4b2a948137a071444ea55a1fa4398ea586a7d09ba0f64f76e7672fb');
  const spec=jobSpec({...raw,casting:{voiceId:' v ',modelId:' model ',unknownVoiceId:' ',cast:{'𐀀':'v2','\ue000':'v1'}}},[' model ']);
  const serialized=encodeSpec(jobSpec({...raw,output:{title:'Cafe\u0301 😀'},tts:{normalizeText:false}}));
  expect(serialized).toContain('"output":{"author":null,"path":"artifact.m4b","source_cover":true,"title":"Cafe\\u0301 \\ud83d\\ude00"}');
  expect(creditsForCharacters(1000000,true,'1_000')).toBe(3000n);
  expect(spec.casting).toMatchObject({cast:[['\ue000','v1'],['𐀀','v2']],unknown_voice_id:'v',model_id:'model'});
});
describe.skipIf(!connectionString)('atomic hosted billing on PostgreSQL',()=>{
  let schema:string, admin:Client, pool:Pool,jobs:JobStore,billing:BillingStore;
  function python(op:string,...args:string[]){return execFileSync('../.venv/bin/python',['test/helpers/billing_database.py',schema,op,...args],{encoding:'utf8'});}
  beforeEach(async()=>{
    expect(connectionString).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
    schema='billing_test_'+randomUUID().replaceAll('-',''); admin=new Client({connectionString:connectionString!,ssl:false});
    await admin.connect();await admin.query('CREATE SCHEMA '+schema);python('init');
    pool=new Pool({connectionString:connectionString!,ssl:false,options:'-c search_path='+schema,application_name:schema,max:6});
    jobs=new JobStore(()=>pool);billing=new BillingStore(()=>pool);
  });
  afterEach(async()=>{await pool?.end();if(admin){await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.end();}});
  async function expectUnusedCandidate(candidate:ReturnType<typeof input>,reason:string){
    const original=randomUUID(),markers=new Set<string>([original]);
    await expect(submitJob(jobs,{
      async arm(id){markers.add(id);},async clear(id){markers.delete(id);},
    },async()=>{throw Error('unexpected dispatch');},candidate)).rejects.toThrow(reason);
    expect(markers).toEqual(new Set([original]));
  }
  async function contend<T>(run:()=>Promise<T>){
    const lock=await pool.connect();await lock.query('BEGIN');await lock.query('SELECT id FROM credit_accounts WHERE id=$1 FOR UPDATE',[account]);
    const pending=Promise.allSettled([run(),run()]);
    try{
      let n=0;const until=Date.now()+4000;
      while(n<2&&Date.now()<until){n=Number((await admin.query("SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock'",[schema])).rows[0].count);
        if(n<2)await new Promise(r=>setTimeout(r,10));}
      expect(n).toBe(2);
    } finally{await lock.query('ROLLBACK');lock.release();}
    return pending;
  }
  it('serializes competing debits, rolls back overdraw and releases exactly once from Python',async()=>{
    const attempts=await contend(()=>jobs.admitJob(input()));
    const ok=attempts.find(x=>x.status==='fulfilled')!;
    expect(attempts.find(x=>x.status==='rejected')).toMatchObject({reason:{message:'insufficient_credits'}});
    if(ok.status!=='fulfilled')throw Error('missing admission');
    python('release',ok.value.jobId);python('release',ok.value.jobId);
    expect((await billing.accountForOwner(owner)).availableCredits).toBe('10');
    expect((await pool.query('SELECT kind,credits FROM credit_ledger_entries ORDER BY created_at,id')).rows.map(x=>[x.kind,x.credits]))
      .toEqual([['purchase',1000],['reservation',-7],['release',7]]);
  });
  it('serializes replay, restores omitted historical defaults, rejects changed intent even when source retired',async()=>{
    const results=await contend(()=>jobs.admitJob(input('')));
    expect(results.filter(x=>x.status==='fulfilled')).toHaveLength(2);
    expect((await pool.query('SELECT * FROM job_idempotency')).rows).toHaveLength(1);
    await pool.query("UPDATE jobs SET spec_json=(spec_json::jsonb - 'tts' || jsonb_build_object('tts','{}'::jsonb,'output',jsonb_build_object('path','artifact.m4b'),'casting',jsonb_build_object('voice_id','narrator')))::text,status='succeeded'");
    await pool.query('UPDATE assets SET deleted_at=now()');
    expect((await jobs.admitJob({...input(''),credits:9n})).status).toBe('succeeded');
    await expectUnusedCandidate({...input(''),spec:jobSpec({...raw,chapters:['other']})},'idempotency_conflict');
    await expect(jobs.admitJob(input())).rejects.toThrow('source_expired');
  });
  it('grants concurrent deliveries once, detects conflicting repeats and preserves large balances',async()=>{
    await expect(billing.grantPayment('cs_zero',account,0)).rejects.toThrow('invalid_credit_amount');
    const secret='fixture',handler=new StripePayments(secret,(...args)=>billing.grantPayment(...args),()=>100);
    let delivery=0;
    const results=await contend(()=>{
      const type=delivery++?'checkout.session.completed':'checkout.session.async_payment_succeeded';
      const raw=Buffer.from(JSON.stringify({id:'evt_'+delivery,type,data:{object:{id:'cs_fixture',client_reference_id:account,payment_status:'paid',
        metadata:{account_id:account,purpose:'kenkui_credits_v2',credits:'1100'},mode:'payment',currency:'usd',
        amount_subtotal:1000,amount_total:1050,total_details:{amount_tax:50,amount_discount:0,amount_shipping:0}}}}));
      const sig='t=100,v1='+createHmac('sha256',secret).update('100.').update(raw).digest('hex');
      return handler.processStripeEvent(raw,sig);
    });
    expect(results.map(x=>x.status)).toEqual(['fulfilled','fulfilled']);
    await expect(billing.grantPayment('cs_fixture',account,500)).rejects.toThrow('payment_reference_conflict');
    expect((await pool.query('SELECT provider,provider_event_id FROM payment_events')).rows).toEqual([{provider:'stripe',provider_event_id:'cs_fixture'}]);
    expect((await pool.query("SELECT credited FROM credit_lots WHERE kind='purchase' AND reference='stripe:cs_fixture'")).rows).toEqual([{credited:'1100'}]);
    await pool.query("UPDATE credit_accounts SET available_credits=9007199254740993");
    expect((await billing.accountForOwner(owner)).availableCredits).toBe('9007199254740993');
  });
  it('creates an allowance once for a mapped identity and preserves the legacy account UUID',async()=>{
    const second=randomUUID();await pool.query('INSERT INTO identities(id) VALUES($1)',[second]);
    const [a,b]=await Promise.all([billing.accountForOwner(second),billing.accountForOwner(second)]);
    expect(a).toEqual(b);expect(a.availableCredits).toBe('1000');
    expect((await pool.query('SELECT kind,credited FROM credit_lots WHERE account_id=$1',[a.id])).rows).toEqual([{kind:'grant',credited:'1000'}]);
    expect((await billing.accountForOwner(owner)).id).toBe(account);
    const before=JSON.parse(python('snapshot'));
    python('allowance',second,a.id);
    expect(JSON.parse(python('snapshot'))).toEqual(before);
    // Recreate the grant through the actual hosted Python resolver on the same account.
    await pool.query('DELETE FROM credit_ledger_entries WHERE account_id=$1',[a.id]);
    await pool.query('DELETE FROM credit_lots WHERE account_id=$1',[a.id]);
    await pool.query('UPDATE credit_accounts SET available_credits=0 WHERE id=$1',[a.id]);
    await pool.query('ALTER TABLE credit_lots ALTER COLUMN sequence RESTART WITH 5');
    python('allowance',second,a.id);expect(JSON.parse(python('snapshot'))).toEqual(before);
    const original='beta-allowance:'+second;
    await pool.query('UPDATE credit_ledger_entries SET credits=500 WHERE reference=$1',[original]);
    await expect(billing.accountForOwner(second)).rejects.toThrow('payment_reference_conflict');
  });
  it('returns exact historical BIGINT JSON tokens and UTC microseconds in sequence order',async()=>{
    await pool.query("UPDATE credit_lots SET credited=9007199254740993, created_at='2026-11-02T18:00:00.123456Z' WHERE id='old'");
    const raw=JSON.stringify(await billing.history(account));
    expect(raw).toContain('"credited":9007199254740993');expect(raw).toContain('"available":9007199254740993');
    expect(raw).toContain('"recordedAt":"2026-11-02T18:00:00.123456Z"');
    expect(JSON.parse(raw).map((x:{usageStatus:string})=>x.usageStatus)).toEqual(['unused','not_purchased','not_purchased','manual_review']);
  });
  it('rolls back mismatching lot totals, caps per-job integers, checks ownership and limits active work',async()=>{
    await expect(jobs.admitJob({...input(),credits:2147483648n})).rejects.toThrow('insufficient_credits');
    await pool.query("UPDATE credit_accounts SET available_credits=2147483648");
    await expect(jobs.admitJob({...input(),credits:2147483648n})).rejects.toThrow('invalid_credit_amount');
    expect((await pool.query('SELECT available_credits FROM credit_accounts')).rows[0].available_credits).toBe('2147483648');
    await expect(jobs.admitJob({...input(),ownerId:randomUUID()})).rejects.toThrow('missing_credit_account');
    await pool.query("UPDATE credit_accounts SET available_credits=20");
    await expect(jobs.admitJob({...input(),credits:11n})).rejects.toThrow('credit_lot_balance_mismatch');
    expect((await pool.query('SELECT * FROM credit_allocations')).rows).toEqual([]);
    expect((await pool.query('SELECT sum(reserved) FROM credit_lots')).rows[0].sum).toBe('0');
    for(let i=0;i<3;i++)await jobs.admitJob({...input(),credits:1n});
    await expectUnusedCandidate(input(),'active_job_limit');
  });
  it.each(['settle','release'])('matches complete Python rows before and after repeated %s',async terminal=>{
    const candidate=input('full');await billing.grantPayment('cs_full',account,1100);await jobs.admitJob(candidate);
    const snapshot=()=>JSON.parse(python('snapshot'));
    const reserved=snapshot();python(terminal,candidate.jobId);python(terminal,candidate.jobId);
    const settled=snapshot();python('reset');python('payment');python('admit',candidate.jobId,candidate.dispatchId);
    expect(snapshot()).toEqual(reserved);python(terminal,candidate.jobId);expect(snapshot()).toEqual(settled);
  });
  it('keeps recovery armed across rollback, ambiguous commit and dispatch; replays without starting a second worker',async()=>{
    const markers=new Set<string>(),calls:string[]=[];
    const signals={arm:async(id:string)=>{calls.push('arm:'+id);markers.add(id);},clear:async(id:string)=>{calls.push('clear:'+id);markers.delete(id);}};
    const denied={...input(),credits:11n};
    await expect(submitJob(jobs,signals,async()=>{},denied)).rejects.toThrow('insufficient_credits');
    expect(markers.size).toBe(0);
    execFileSync('../.venv/bin/python',['-c',`import json,sys
from types import SimpleNamespace
from kenkui_server.maintenance import recover_with_signals
def forbidden(): raise AssertionError("empty minute opened a database")
recover_with_signals(SimpleNamespace(list_job_ids=lambda:json.load(sys.stdin)),forbidden,forbidden)`],
      {input:JSON.stringify([...markers])});
    await expect(submitJob(jobs,{...signals,clear:async()=>{throw Error('cleanup failed');}},async()=>{},denied)).rejects.toThrow('insufficient_credits');
    markers.delete(denied.jobId);
    const pending=input('crash');
    await expect(submitJob(jobs,{...signals,arm:async()=>{throw Error('failed R2 arm');}},async()=>{},pending)).rejects.toThrow('failed R2 arm');
    expect((await pool.query('SELECT * FROM credit_authorizations')).rows).toEqual([]);
    await pool.query("ALTER TABLE dispatches ADD CHECK(status <> 'pending')");
    const dispatch=async(id:string)=>{calls.push('dispatch:'+id);throw Error('lost HTTP response');};
    await expect(submitJob(jobs,signals,dispatch,pending)).rejects.toThrow();
    expect(markers.has(pending.jobId)).toBe(true);expect((await pool.query('SELECT * FROM credit_authorizations')).rows).toEqual([]);
    await pool.query('ALTER TABLE dispatches DROP CONSTRAINT dispatches_status_check');
    await expect(submitJob({admitJob:async i=>{await jobs.admitJob(i);throw Error('lost commit');}},signals,dispatch,pending)).rejects.toThrow('lost commit');
    expect(calls.every(c=>!c.startsWith('dispatch:'))).toBe(true);
    const replay=input('crash');await submitJob(jobs,signals,dispatch,replay);
    expect(calls.slice(-3)).toEqual(['arm:'+replay.jobId,'arm:'+pending.jobId,'clear:'+replay.jobId]);
    const next={...input(),credits:1n};await expect(submitJob(jobs,signals,dispatch,next)).rejects.toThrow('lost HTTP response');
    const accepted={...input(),credits:1n};
    await submitJob(jobs,signals,async id=>{
      const visible=await pool.query('SELECT job_id FROM dispatches WHERE id=$1',[id]);
      expect(visible.rows).toEqual([{job_id:accepted.jobId}]);return {accepted:true};
    },accepted);
    expect(markers).toEqual(new Set([pending.jobId,next.jobId,accepted.jobId]));
  });
});
