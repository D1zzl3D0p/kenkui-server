import { randomUUID } from 'node:crypto';
import { parseExactJson } from '../billing/integers.js';
import type { Pool, PoolClient } from 'pg';
import { decodeSpec, encodeSpec, scopedKey, type JobSpec } from '../billing/job-spec.js';
import { requireUuid } from '../compute/modal.js';
import { ApiError } from '../types.js';
import { transaction } from './transaction.js';
import { streamQuery } from './stream-query.js';

export interface AdmissionInput {
  jobId:string; dispatchId:string; ownerId:string; accountId:string; spec:JobSpec;
  credits:bigint; idempotencyKey:string|null;
}
export interface AdmissionResult {
  jobId:string;dispatchId:string;spec:JobSpec;status:string;version:number;progressJson:string;replayed:boolean;
}
const conflict=(code:string)=>new ApiError(409,'http_error',code);
const queuedProgress='{"completed":0,"stage":"queued","total":0}';

async function reserveLots(client:PoolClient,account:string,authorization:string,credits:bigint){
  const lots=await client.query<{id:string;available:string}>(`SELECT id,credited-reserved-consumed AS available FROM credit_lots
    WHERE account_id=$1 AND credited>reserved+consumed ORDER BY (kind='purchase'),sequence FOR UPDATE`,[account]);
  let remaining=credits;
  for(const row of lots.rows){
    const available=BigInt(row.available), amount=remaining<available?remaining:available;
    await client.query('UPDATE credit_lots SET reserved=reserved+$1 WHERE id=$2',[amount.toString(),row.id]);
    await client.query('INSERT INTO credit_allocations (authorization_id,lot_id,account_id,credits) VALUES ($1,$2,$3,$4)',
      [authorization,row.id,account,amount.toString()]);
    remaining-=amount;
    if(!remaining)return;
  }
  throw Error('credit_lot_balance_mismatch');
}
export class JobStore {
  constructor(private readonly pool:()=>Pool){}
  async listOwned(owner:string):Promise<JobSnapshot[]>{
    requireUuid(owner);
    const rows=await this.pool().query('SELECT id,spec_json,status,version,progress_json,owner_id FROM jobs WHERE owner_id=$1 ORDER BY id',[owner]);
    return rows.rows.map(snapshot);
  }
  async getOwned(id:string,owner:string,signal?:AbortSignal):Promise<JobSnapshot>{
    requireUuid(owner);
    const sql='SELECT j.id,j.spec_json,j.status,j.version,j.progress_json,j.owner_id,f.code,f.message FROM jobs j LEFT JOIN job_failures f ON f.job_id=j.id WHERE j.id=$1';
    const row=signal?(await streamQuery(this.pool,sql,[id],signal))[0]:(await this.pool().query(sql,[id])).rows[0];
    return ownedSnapshot(row,owner);
  }
  async artifactForJob(id:string):Promise<string>{
    const rows=(await this.pool().query('SELECT path FROM artifacts WHERE job_id=$1',[id])).rows;
    if(rows.length!==1)throw new ApiError(404,'not_found','Not found');
    return rows[0].path;
  }
  async cancelOwned(id:string,owner:string):Promise<JobSnapshot>{
    requireUuid(owner);
    return transaction(this.pool(),async client=>{
      const row=(await client.query('SELECT id,spec_json,status,version,progress_json,owner_id FROM jobs WHERE id=$1',[id])).rows[0];
      const current=ownedSnapshot(row,owner);
      if(['succeeded','failed','cancelled','cancel_requested'].includes(current.status))return current;
      const dispatch=(await client.query('SELECT status FROM dispatches WHERE job_id=$1',[id])).rows[0];
      const progressJson=canonicalProgress(current.progressJson);
      const status=current.status==='queued'||(dispatch&&!['pending','running'].includes(dispatch.status))?'cancelled':'cancel_requested';
      // Same optimistic job UPDATE -> account -> authorization -> lots order as Python.
      // A stale optimistic write aborts the whole transaction; never replay financial substeps.
      const result=await client.query('UPDATE jobs SET status=$1,version=$2,progress_json=$3 WHERE id=$4 AND version=$5',
        [status,current.version+1,progressJson,id,current.version]);
      if(result.rowCount!==1)throw Error('stale_write');
      const sequence=(await client.query('SELECT COALESCE(MAX(sequence),0)+1 AS sequence FROM job_events WHERE job_id=$1',[id])).rows[0].sequence;
      await client.query('INSERT INTO job_events(job_id,sequence,event_type,progress_json) VALUES($1,$2,$3,$4)',[id,sequence,status,progressJson]);
      if(status==='cancelled'){
        await client.query('UPDATE jobs SET terminal_at=now() WHERE id=$1',[id]);
        await releaseCredits(client,id);
      }
      return {...current,status,progressJson,version:current.version+1};
    });
  }
  async admitJob(input:AdmissionInput):Promise<AdmissionResult>{
    const {jobId,dispatchId,spec,ownerId,accountId,credits}=input;
    requireUuid(jobId);requireUuid(dispatchId);requireUuid(ownerId);requireUuid(accountId);
    if(credits<1n)throw conflict('empty_speech');
    const key=input.idempotencyKey===null?null:scopedKey(ownerId,input.idempotencyKey);
    return transaction(this.pool(),async client=>{
      const account=await client.query('SELECT id FROM credit_accounts WHERE id=$1 AND identity_id=$2 FOR UPDATE',[accountId,ownerId]);
      if(!account.rowCount)throw Error('missing_credit_account');
      if(key!==null){
        const existing=(await client.query(`SELECT j.id,j.spec_json,j.status,j.version,j.progress_json,d.id AS dispatch_id
          FROM job_idempotency k JOIN jobs j ON j.id=k.job_id JOIN dispatches d ON d.job_id=j.id
          WHERE k.key=$1 AND j.owner_id=$2`,[key,ownerId])).rows[0];
        if(existing){
          const stored=decodeSpec(existing.spec_json);
          if(encodeSpec(stored)!==encodeSpec(spec))throw conflict('idempotency_conflict');
          return {jobId:existing.id,dispatchId:existing.dispatch_id,status:existing.status,version:existing.version,
            progressJson:existing.progress_json,spec:stored,replayed:true};
        }
      }
      const active=await client.query("SELECT count(*) FROM jobs WHERE owner_id=$1 AND status NOT IN ('succeeded','failed','cancelled')",[ownerId]);
      if(BigInt(active.rows[0].count)>=3n)throw conflict('active_job_limit');
      const source=(await client.query('SELECT owner_id,deleted_at FROM assets WHERE id=$1 FOR UPDATE',[spec.source_id])).rows[0];
      if(!source)throw new ApiError(404,'not_found','Not found');
      if(source.owner_id!==ownerId)throw new ApiError(403,'http_error','resource is not owned by this user');
      if(source.deleted_at!==null)throw conflict('source_expired');
      if(credits>9223372036854775807n)throw conflict('insufficient_credits');
      const amount=credits.toString(),authorization=randomUUID();
      const debit=await client.query('UPDATE credit_accounts SET available_credits=available_credits-$1 WHERE id=$2 AND available_credits >= $1',[amount,accountId]);
      if(!debit.rowCount)throw conflict('insufficient_credits');
      if(credits>2147483647n)throw Error('invalid_credit_amount');
      await client.query("INSERT INTO credit_authorizations(id,job_id,account_id,credits,status) VALUES ($1,$2,$3,$4,'reserved')",[authorization,jobId,accountId,amount]);
      await reserveLots(client,accountId,authorization,credits);
      await client.query(`INSERT INTO credit_ledger_entries(id,account_id,authorization_id,kind,credits,reference)
        VALUES ($1,$2,$3,'reservation',$4,$5)`,[randomUUID(),accountId,authorization,(-credits).toString(),'reservation:'+jobId]);
      await client.query(`INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id)
        VALUES ($1,$2,'queued',0,$3,$4)`,[jobId,encodeSpec(spec),queuedProgress,ownerId]);
      await client.query("INSERT INTO dispatches(id,job_id,status,version) VALUES ($1,$2,'pending',0)",[dispatchId,jobId]);
      if(key!==null)await client.query('INSERT INTO job_idempotency(key,job_id) VALUES ($1,$2)',[key,jobId]);
      return {jobId,dispatchId,status:'queued',version:0,progressJson:queuedProgress,spec,replayed:false};
    });
  }
}
export interface RecoverySignals {arm(id:string):Promise<void>;clear(id:string):Promise<void>}
/** Use fresh candidate IDs on every call. Keep ambiguous or committed markers for Python maintenance. */
export async function submitJob(store:Pick<JobStore,'admitJob'>,signals:RecoverySignals,
  dispatch:(id:string)=>Promise<unknown>,input:AdmissionInput):Promise<AdmissionResult>{
  if(input.credits<1n)throw conflict('empty_speech');
  await signals.arm(input.jobId);
  const admitted=await store.admitJob(input);
  if(admitted.replayed){
    await signals.arm(admitted.jobId);
    if(admitted.jobId!==input.jobId)await signals.clear(input.jobId);
  }else await dispatch(admitted.dispatchId);
  return admitted;
}
function canonicalProgress(raw:string):string {
  const p=parseExactJson(raw) as {completed:bigint;stage:string;total:bigint};
  return JSON.stringify({completed:p.completed,stage:p.stage,total:p.total},(_key,value)=>typeof value==='bigint'?(JSON as JSON&{rawJSON(s:string):unknown}).rawJSON(String(value)):value)
    .replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
interface JobRow {id:string;spec_json:string;status:string;version:number;progress_json:string;owner_id:string;code?:string|null;message?:string|null}
export interface JobSnapshot {jobId:string;spec:JobSpec;status:string;version:number;progressJson:string;failure?:Record<string,string>|null}
function snapshot(row:JobRow):JobSnapshot {
  return {jobId:row.id,spec:decodeSpec(row.spec_json),status:row.status,version:row.version,progressJson:row.progress_json,
    ...(row.code&&row.message!==null&&row.message!==undefined?{failure:{code:row.code,message:row.message}}:{})};
}
function ownedSnapshot(row:JobRow|undefined,owner:string):JobSnapshot {
  if(!row)throw new ApiError(404,'not_found','Not found');
  if(row.owner_id!==owner)throw new ApiError(403,'http_error','resource is not owned by this user');
  return snapshot(row);
}
/** Narrow port of Python billing.finalize(RELEASED), within the caller's job transaction. */
async function releaseCredits(client:PoolClient,jobId:string):Promise<void>{
  const first=(await client.query('SELECT account_id FROM credit_authorizations WHERE job_id=$1',[jobId])).rows[0];
  if(!first)throw Error('missing_credit_authorization');
  await client.query('SELECT id FROM credit_accounts WHERE id=$1 FOR UPDATE',[first.account_id]);
  const authorization=(await client.query('SELECT id,account_id,credits,status FROM credit_authorizations WHERE job_id=$1 FOR UPDATE',[jobId])).rows[0];
  if(!authorization)throw Error('missing_credit_authorization');
  if(authorization.status!=='reserved')return;
  const allocations=(await client.query('SELECT lot_id,credits FROM credit_allocations WHERE authorization_id=$1 AND account_id=$2',[authorization.id,authorization.account_id])).rows;
  if(allocations.reduce((sum:bigint,a)=>sum+BigInt(a.credits),0n)!==BigInt(authorization.credits))throw Error('credit_lot_balance_mismatch');
  for(const allocation of allocations){
    const changed=await client.query('UPDATE credit_lots SET reserved=reserved-$1,consumed=consumed+0 WHERE id=$2 AND account_id=$3 AND reserved >= $1',
      [allocation.credits,allocation.lot_id,authorization.account_id]);
    if(changed.rowCount!==1)throw Error('credit_lot_balance_mismatch');
  }
  const changed=await client.query("UPDATE credit_authorizations SET status='released',finalized_at=now() WHERE id=$1 AND status='reserved'",[authorization.id]);
  if(changed.rowCount!==1)throw Error('stale_write');
  await client.query('UPDATE credit_accounts SET available_credits=available_credits+$1 WHERE id=$2',[authorization.credits,authorization.account_id]);
  await client.query("INSERT INTO credit_ledger_entries(id,account_id,authorization_id,kind,credits,reference) VALUES($1,$2,$3,'release',$4,$5)",
    [randomUUID(),authorization.account_id,authorization.id,authorization.credits,'release:'+jobId]);
}
