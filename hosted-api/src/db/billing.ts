import { integerText } from '../billing/integers.js';
import { randomUUID } from 'node:crypto';
import type { Pool, PoolClient } from 'pg';
import { transaction } from './transaction.js';

export class PaymentReferenceConflict extends Error {
  constructor(){super('payment_reference_conflict');}
}
async function grant(client:PoolClient,account:string,credits:number,reference:string,kind:'grant'|'purchase'):Promise<void>{
  await client.query('SELECT id FROM credit_accounts WHERE id=$1 FOR UPDATE',[account]);
  const entry=await client.query(`INSERT INTO credit_ledger_entries(id,account_id,authorization_id,kind,credits,reference)
    VALUES ($1,$2,NULL,'purchase',$3,$4) ON CONFLICT(reference) DO NOTHING RETURNING id`,[randomUUID(),account,credits,reference]);
  if(!entry.rowCount){
    const previous=(await client.query('SELECT account_id,credits FROM credit_ledger_entries WHERE reference=$1',[reference])).rows[0];
    if(!previous || previous.account_id!==account || previous.credits!==credits)throw new PaymentReferenceConflict();
    return;
  }
  await client.query('INSERT INTO credit_lots(id,account_id,kind,reference,credited) VALUES ($1,$2,$3,$4,$5)',
    [randomUUID(),account,kind,reference,credits]);
  await client.query('UPDATE credit_accounts SET available_credits=available_credits+$1 WHERE id=$2',[credits,account]);
}

export class BillingStore {
  constructor(private readonly pool:()=>Pool){}
  async accountForOwner(owner:string):Promise<{id:string;availableCredits:string}>{
    const configured=integerText(process.env.KENKUI_BETA_CREDITS??'1000');
    if(configured===null||configured<1n||configured>2147483647n)throw Error('invalid_beta_allowance');
    return transaction(this.pool(),async client=>{
      const row=(await client.query(`INSERT INTO credit_accounts(id,identity_id) VALUES($1,$2)
        ON CONFLICT(identity_id) DO UPDATE SET identity_id=EXCLUDED.identity_id RETURNING id`,[randomUUID(),owner])).rows[0];
      await grant(client,row.id,Number(configured),'beta-allowance:'+owner,'grant');
      const result=await client.query('SELECT available_credits FROM credit_accounts WHERE id=$1',[row.id]);
      return {id:row.id,availableCredits:result.rows[0].available_credits};
    });
  }
  async history(account:string){
    // pg returns BIGINT as text. Emit exact JSON integer tokens to preserve the Python wire schema.
    const {rows}=await this.pool().query(`SELECT id,kind AS source,reference,credited,credited-reserved-consumed AS available,reserved,consumed,
      to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
      CASE WHEN extract(microseconds FROM created_at)::bigint % 1000000 = 0 THEN '' ELSE '.' || to_char(created_at,'US') END || 'Z' AS "recordedAt",
      CASE WHEN kind='legacy' THEN 'manual_review' WHEN kind<>'purchase' THEN 'not_purchased'
           WHEN consumed>0 THEN 'used' WHEN reserved>0 THEN 'reserved' ELSE 'unused' END AS "usageStatus"
      FROM credit_lots WHERE account_id=$1 ORDER BY sequence`,[account]);
    const rawInteger=(value:string)=>(JSON as JSON & {rawJSON(s:string):unknown}).rawJSON(value);
    return rows.map(({id,source,reference,credited,available,reserved,consumed,recordedAt,usageStatus})=>({
      id,source,reference,credited:rawInteger(credited),available:rawInteger(available),reserved:rawInteger(reserved),consumed:rawInteger(consumed),recordedAt,usageStatus,
    }));
  }
  async grantPayment(sessionId:string,account:string,credits:number):Promise<void>{
    if(!Number.isInteger(credits)||credits<1||credits>2147483647)throw Error('invalid_credit_amount');
    await transaction(this.pool(),async client=>{
      const reference='stripe:'+sessionId;
      await client.query("INSERT INTO payment_events(provider,provider_event_id) VALUES ('stripe',$1) ON CONFLICT DO NOTHING",[sessionId]);
      await grant(client,account,credits,reference,'purchase');
    });
  }
}
