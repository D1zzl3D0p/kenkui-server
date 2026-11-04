import type { Pool } from 'pg';
import { createIdentity, findIdentity, lockIdentity, IdentityError } from '../db/identities.js';
import { blocked, candidates, currentProfile, guarded, mailboxKey, unavailable, type Environment } from '../db/migration.js';
import { ApiError } from '../types.js';
import type { VerifiedToken } from './neon.js';

export class Admission {
  constructor(private pool:()=>Pool,private environment:Environment,private issuer:string){}
  onboard(who:VerifiedToken):Promise<{userId:string}> {
    let enrollmentEmail:string|undefined;
    return guarded(this.pool,this.environment,this.issuer,who,async(client,run,profile)=>{
      const existing=await findIdentity(client,who.issuer,who.subject);
      if(existing) return {userId:existing};
      const key=mailboxKey(profile.email);
      if(!profile.emailVerified||!profile.email||!key) throw blocked();
      if(run.invited_emails.length&&!run.invited_emails.includes(key)) throw blocked();
      const matches=await candidates(client,run.id,key);
      if(matches.length>1||matches.some(row=>row.owner_id!==null||!row.verified)) throw blocked();
      enrollmentEmail=profile.email;
      return {userId:await createIdentity(client,who.issuer,who.subject,profile.email.trim())};
    },latest=>{
      if(enrollmentEmail!==undefined&&(!latest.emailVerified||latest.email!==enrollmentEmail)) throw blocked();
    });
  }
}

/** Fresh Neon enrollment does not depend on an imported WorkOS directory. */
export class NeonAdmission {
  constructor(private pool:()=>Pool,private issuer:string){}
  async onboard(who:VerifiedToken):Promise<{userId:string}> {
    const client=await this.pool().connect();
    let discard=false;
    try {
      await client.query('BEGIN');
      await lockIdentity(client,who.issuer,who.subject);
      const profile=await currentProfile(client,who,this.issuer);
      const existing=await findIdentity(client,who.issuer,who.subject);
      if(existing) {
        await currentProfile(client,who,this.issuer);
        await client.query('COMMIT');
        return {userId:existing};
      }
      if(!profile.emailVerified||!profile.email||!mailboxKey(profile.email)) throw blocked();
      const userId=await createIdentity(client,who.issuer,who.subject,profile.email.trim());
      const latest=await currentProfile(client,who,this.issuer);
      if(!latest.emailVerified||latest.email!==profile.email) throw blocked();
      await client.query('COMMIT');
      return {userId};
    } catch(error) {
      try {await client.query('ROLLBACK');} catch {discard=true;throw unavailable();}
      if(error instanceof ApiError) throw error;
      if(error instanceof IdentityError) throw blocked();
      throw unavailable();
    } finally {client.release(discard);}
  }
}
