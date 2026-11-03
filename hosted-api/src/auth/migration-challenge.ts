import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import type { Pool,PoolClient } from 'pg';
import { ApiError } from '../types.js';
import { findIdentity, IdentityError } from '../db/identities.js';
import { activeRun,blocked,candidates,currentProfile,guarded,mailboxKey,unavailable,type Environment } from '../db/migration.js';
import { linkOnClient } from './linking.js';
import type { VerifiedToken } from './neon.js';

const alphabet='0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const uuid=/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
function digest(id:string,code:string):Buffer {
  return createHash('sha256').update(['kenkui-migration-v1',id,code].join('\0'),'ascii').digest();
}
export interface MigrationMail {ready():Promise<void>; deliver(id:string,code:string):Promise<void>}

async function rate(client:PoolClient,kind:'issue'|'owner'|'verify',key:string):Promise<number> {
  // Subject guard serializes caller events. Separate owner guard spans all callers.
  if(kind==='owner') await client.query('SELECT pg_advisory_xact_lock(915003,hashtext($1))',[key]);
  const {rows}=await client.query<{age:number}>(`SELECT extract(epoch FROM(clock_timestamp()-created_at))::float8 age
    FROM migration_rate_events WHERE kind=$1 AND rate_key=$2 AND created_at>clock_timestamp()-interval '1 day'
    ORDER BY created_at DESC`,[kind,key]);
  const ages=rows.map(row=>row.age);
  const timeLeft=(cap:number,seconds:number)=>Math.max(0,seconds-(ages[cap-1]??Infinity));
  const seconds=kind==='verify'?timeLeft(60,3600):
    Math.max(timeLeft(1,60),timeLeft(3,3600),timeLeft(10,86400));
  if(seconds>0) return Math.ceil(seconds);
  await client.query('INSERT INTO migration_rate_events(kind,rate_key) VALUES ($1,$2)',[kind,key]);
  return 0;
}
function limited(seconds:number):never {
  throw new ApiError(429,'rate_limited','Try again later.',{},{'Retry-After':String(seconds)});
}

export async function mintAndLink(client:PoolClient,who:VerifiedToken,owner:string,
  method:'legacy_session'|'verified_legacy_email_challenge'):Promise<string>{
  const proofId=randomUUID();
  await client.query(`INSERT INTO identity_link_proofs(id,identity_id,issuer,subject,verification_method,expires_at)
    VALUES ($1,$2,$3,$4,$5,clock_timestamp()+interval '5 minutes')`,
    [proofId,owner,who.issuer,who.subject,method]);
  return linkOnClient(client,{issuer:who.issuer,subject:who.subject,internalUserId:owner,proofId},true);
}

export class MigrationChallenges {
  constructor(private pool:()=>Pool,private environment:Environment,private issuer:string,
    private mail:()=>MigrationMail,private now=()=>performance.now(),
    private sleep=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms))){}
  async issue(who:VerifiedToken,address:string):Promise<{challengeId:string}> {
    const key=mailboxKey(address);
    if(!key) throw new ApiError(422,'validation_error','Request validation failed');
    const start=this.now();
    let sender:MigrationMail;
    try {sender=this.mail();await sender.ready();} catch {throw unavailable();}
    const challengeId=randomUUID();
    const code=Array.from(randomBytes(16),byte=>alphabet[byte&31]).join('');
    const deliver=await guarded(this.pool,this.environment,this.issuer,who,async(client,run)=>{
      const wait=await rate(client,'issue',JSON.stringify([who.issuer,who.subject]));
      if(wait) limited(wait);
      const matches=await candidates(client,run.id,key);
      const candidate=matches[0];
      if(matches.length!==1||!candidate?.eligible||!candidate.owner_id) return false;
      const owner=candidate.owner_id;
      const existing=await findIdentity(client,who.issuer,who.subject);
      if(existing&&existing!==owner) return false;
      if(await rate(client,'owner',owner)) return false;
      await activeRun(client,this.environment);
      await currentProfile(client,who,this.issuer);
      await client.query(`UPDATE migration_challenges SET superseded_at=clock_timestamp()
        WHERE issuer=$1 AND subject=$2 AND owner_id=$3 AND consumed_at IS NULL AND superseded_at IS NULL`,
        [who.issuer,who.subject,owner]);
      await client.query(`INSERT INTO migration_challenges(id,run_id,issuer,subject,old_subject,owner_id,digest,expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,clock_timestamp()+interval '10 minutes')`,
        [challengeId,run.id,who.issuer,who.subject,candidate.subject,owner,digest(challengeId,code)]);
      return true;
    });
    if(deliver) {
      try {await sender.deliver(challengeId,code);} catch { /* A timed out dispatch may have sent. */ }
    }
    await this.sleep(Math.max(0,2000-(this.now()-start)));
    return {challengeId};
  }

  async verify(who:VerifiedToken,id:string,raw:string):Promise<{userId:string}> {
    const code=raw.replace(/[ -]/g,'');
    const canonical=/^[a-zA-Z0-9]{16}$/.test(code)?code.toUpperCase():'';
    const userId=await guarded(this.pool,this.environment,this.issuer,who,async(client,run)=>{
      const wait=await rate(client,'verify',JSON.stringify([who.issuer,who.subject]));
      if(wait) limited(wait);
      if(!uuid.test(id)) return null;
      const challenge=(await client.query<{owner_id:string;digest:Buffer}>(`SELECT c.owner_id,c.digest
        FROM migration_challenges c JOIN migration_directory_rows r ON r.run_id=c.run_id AND r.subject=c.old_subject
        JOIN provider_identities p ON p.issuer=$5 AND p.subject=r.subject AND p.identity_id=r.owner_id
        WHERE c.id=$1 AND c.run_id=$2 AND c.issuer=$3 AND c.subject=$4 AND r.owner_id=c.owner_id AND r.verified AND NOT r.banned
          AND c.attempts<5 AND c.expires_at>clock_timestamp() AND c.consumed_at IS NULL AND c.superseded_at IS NULL
        FOR UPDATE OF c`,[id,run.id,who.issuer,who.subject,run.issuer])).rows[0];
      if(!challenge) return null;
      if(!/^[0-9A-HJKMNP-TV-Z]{16}$/.test(canonical)||!timingSafeEqual(challenge.digest,digest(id,canonical))) {
        await client.query('UPDATE migration_challenges SET attempts=attempts+1 WHERE id=$1',[id]);
        return null;
      }
      const existing=await findIdentity(client,who.issuer,who.subject);
      if(existing&&existing!==challenge.owner_id) return null;
      // A denial after lock contention must still commit the caller's rate event.
      // Database faults abort the whole transaction and never leave partial proof evidence.
      await client.query('SAVEPOINT verify_binding');
      try {
        const owner=await mintAndLink(client,who,challenge.owner_id,'verified_legacy_email_challenge');
        const consumed=await client.query(`UPDATE migration_challenges SET consumed_at=clock_timestamp()
          WHERE id=$1 AND expires_at>clock_timestamp() AND consumed_at IS NULL`,[id]);
        if(!consumed.rowCount) throw new IdentityError('invalid_proof');
        return owner;
      } catch(error) {
        if(!(error instanceof IdentityError)) throw error;
        await client.query('ROLLBACK TO SAVEPOINT verify_binding');
        return null;
      }
    });
    if(!userId) throw blocked();
    return {userId};
  }
}
