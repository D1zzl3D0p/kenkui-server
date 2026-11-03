import type { Pool } from 'pg';
import { createIdentity, findIdentity } from '../db/identities.js';
import { blocked, candidates, guarded, mailboxKey, type Environment } from '../db/migration.js';
import type { VerifiedToken } from './neon.js';

export class Admission {
  constructor(private pool:()=>Pool,private environment:Environment,private issuer:string){}
  onboard(who:VerifiedToken):Promise<{userId:string}> {
    return guarded(this.pool,this.environment,this.issuer,who,async(client,run,profile)=>{
      const existing=await findIdentity(client,who.issuer,who.subject);
      if(existing) return {userId:existing};
      const key=mailboxKey(profile.email);
      if(!profile.emailVerified||!profile.email||!key) throw blocked();
      if(run.invited_emails.length&&!run.invited_emails.includes(key)) throw blocked();
      const matches=await candidates(client,run.id,key);
      if(matches.length>1||matches.some(row=>row.owner_id!==null||!row.verified)) throw blocked();
      return {userId:await createIdentity(client,who.issuer,who.subject,profile.email.trim())};
    });
  }
}
