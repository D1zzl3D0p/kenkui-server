import { randomUUID } from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import { decodeSpec, encodeSpec, scopedKey, type JobSpec } from '../billing/job-spec.js';
import { requireUuid } from '../compute/modal.js';
import { ApiError } from '../types.js';
import { transaction } from './transaction.js';

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
