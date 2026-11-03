import { createRemoteJWKSet,jwtVerify,type JWTVerifyGetKey } from 'jose';
import type { Pool } from 'pg';
import { ApiError } from '../types.js';
import { blocked,guarded,type Environment } from '../db/migration.js';
import { mintAndLink } from './migration-challenge.js';
import { checkedSigningKeys,httpsUrl,type NeonAuth } from './neon.js';

export const LEGACY_SOURCE={environmentId:'environment_01M0Y347Z9N6FYNTGVY3725X1Y',clientId:'client_01M0Y37EQMGD270AW5W7NZC1E0'} as const;
const legacyAuthority=`urn:kenkui:workos:${LEGACY_SOURCE.environmentId}:${LEGACY_SOURCE.clientId}`;
/** Explicit issuer obtained from the source client configuration, never from HTTP claims. */
export function createLegacyVerifier(issuer:string,keySet:JWTVerifyGetKey=createRemoteJWKSet(new URL(`https://api.workos.com/sso/jwks/${LEGACY_SOURCE.clientId}`),{timeoutDuration:5000})) {
  httpsUrl(issuer);
  const signingKey=checkedSigningKeys(keySet);
  return async(token:string):Promise<string|null>=>{
    if(token.length>16_384) return null;
    try {
      const {payload}=await jwtVerify(token,signingKey,{issuer,algorithms:['RS256'],requiredClaims:['iss','sub','exp','iat','sid','client_id']});
      if(payload.client_id!==LEGACY_SOURCE.clientId||!payload.sub?.startsWith('user_')||typeof payload.sid!=='string'||!payload.sid.startsWith('session_')||payload.act) return null;
      return payload.sub;
    } catch(error) {
      if(error instanceof ApiError) throw error;
      return null;
    }
  };
}

/** Only this authenticated boundary mints production proofs. No email/UUID body input. */
export class LegacyRecovery {
  constructor(private auth:NeonAuth,private verifyLegacy:(token:string)=>Promise<string|null>,
    private pool:()=>Pool,private environment:Environment,private issuer:string) {}
  async link(request:Request,legacyAccessToken:string):Promise<{userId:string}> {
    const who=await this.auth.authenticateRequest(request);
    if(!who) throw new ApiError(401,'unauthenticated','Sign in to continue.');
    const oldSubject=await this.verifyLegacy(legacyAccessToken);
    if(!oldSubject) throw blocked();
    return guarded(this.pool,this.environment,this.issuer,who,async(client,run)=>{
      if(run.issuer!==legacyAuthority) throw blocked();
      const result=await client.query<{owner_id:string}>(`SELECT r.owner_id FROM migration_directory_rows r
        JOIN provider_identities p ON p.issuer=$3 AND p.subject=r.subject AND p.identity_id=r.owner_id
        WHERE r.run_id=$1 AND r.subject=$2 AND NOT r.banned`,[run.id,oldSubject,legacyAuthority]);
      const owner=result.rows[0]?.owner_id;
      if(!owner) throw blocked();
      return {userId:await mintAndLink(client,who,owner,'legacy_session')};
    });
  }
}
