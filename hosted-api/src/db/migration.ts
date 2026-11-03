import type { Pool, PoolClient } from 'pg';
import type { VerifiedToken } from '../auth/neon.js';
import { ApiError } from '../types.js';
import { lockDirectory, lockIdentity, IdentityError } from './identities.js';

export const blocked=()=>new ApiError(409,'recovery_required','Verify your existing account before continuing.');
export const unavailable=()=>new ApiError(503,'migration_unavailable','Account migration is temporarily unavailable.');
export type Environment='staging'|'production';
export interface Run {id:string; issuer:string; invited_emails:string[]}
export interface Profile {email:string|null; emailVerified:boolean}

export function mailboxKey(input:unknown):string|null {
  if(typeof input!=='string') return null;
  const email=input.replace(/^ +| +$/g,'');
  if(email.length>254||/[^\x21-\x7e]/.test(email)) return null;
  const [local,domain,...extra]=email.split('@');
  if(!local||local.length>64||!domain||extra.length) return null;
  if(!local.split('.').every(part=>/^[a-z0-9!#$%&'*+/=?^_`{|}~-]+$/i.test(part))) return null;
  const labels=domain.split('.');
  if(labels.length<2||!labels.every(part=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(part))) return null;
  return email.toLowerCase();
}

export async function activeRun(client:PoolClient,environment:Environment):Promise<Run>{
  const result=await client.query<Run>(`SELECT r.id,r.issuer,r.invited_emails FROM migration_directory_active a
    JOIN migration_directory_runs r ON r.id=a.run_id AND r.environment=a.environment
    WHERE a.environment=$1 AND (r.mode='final' AND r.frozen OR r.valid_until>clock_timestamp())`,[environment]);
  if(!result.rows[0]) throw unavailable();
  return result.rows[0];
}

export async function currentProfile(client:PoolClient,who:VerifiedToken,issuer:string):Promise<Profile>{
  if(who.issuer!==issuer||who.expiresAt<=Date.now()/1000) {
    throw new ApiError(401,'unauthenticated','Sign in to continue.');
  }
  if(!/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(who.subject)) throw blocked();
  const result=await client.query<Profile>(`SELECT email,"emailVerified" FROM neon_auth."user"
    WHERE id=$1::uuid AND banned IS NOT TRUE`,[who.subject]);
  if(!result.rows[0]) throw blocked();
  return result.rows[0];
}

export async function guarded<T>(pool:()=>Pool,env:Environment,issuer:string,who:VerifiedToken,
  work:(client:PoolClient,run:Run,profile:Profile)=>Promise<T>,
  validateLatest?:(profile:Profile)=>void):Promise<T>{
  let client:PoolClient|undefined;
  let discard=false;
  try {
    client=await pool().connect();
    await client.query('BEGIN');
    await lockDirectory(client,env);
    await lockIdentity(client,who.issuer,who.subject);
    const run=await activeRun(client,env);
    const profile=await currentProfile(client,who,issuer);
    const result=await work(client,run,profile);
    // Decisions may have waited on an owner, proof or binding; recheck before commit.
    await activeRun(client,env);
    const latest=await currentProfile(client,who,issuer);
    validateLatest?.(latest);
    await client.query('COMMIT');
    return result;
  } catch(error) {
    try {
      await client?.query('ROLLBACK');
    } catch {
      discard=true;
      throw unavailable();
    }
    if(error instanceof ApiError) throw error;
    if(error instanceof IdentityError) throw blocked();
    throw unavailable();
  } finally {client?.release(discard);}
}

export async function candidates(client:PoolClient,run:string,key:string) {
  return (await client.query<{subject:string;owner_id:string|null;eligible:boolean;verified:boolean}>(`
    SELECT r.subject,r.owner_id,r.verified AND NOT r.banned AND r.address_key=$2 AS eligible,r.verified AND NOT r.banned AS verified
    FROM migration_directory_rows r WHERE r.run_id=$1 AND
    (r.address_key=$2 OR EXISTS (SELECT 1 FROM migration_directory_aliases a
      WHERE a.run_id=r.run_id AND a.owner_id=r.owner_id AND a.address_key=$2))`,[run,key])).rows;
}
