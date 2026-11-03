import { randomUUID } from 'node:crypto';
import { createRemoteJWKSet,jwtVerify,type JWTVerifyGetKey } from 'jose';
import type { Pool } from 'pg';
import { ApiError } from '../types.js';
import { IdentityError,type IdentityStore } from '../db/identities.js';
import type { IdentityLinker } from './linking.js';
import { httpsUrl,type NeonAuth } from './neon.js';

export const LEGACY_SOURCE={environmentId:'environment_01M0Y347Z9N6FYNTGVY3725X1Y',clientId:'client_01M0Y37EQMGD270AW5W7NZC1E0'} as const;
const legacyAuthority=`urn:kenkui:workos:${LEGACY_SOURCE.environmentId}:${LEGACY_SOURCE.clientId}`;
/** Explicit issuer obtained from the source client configuration, never from HTTP claims. */
export function createLegacyVerifier(issuer:string,keySet:JWTVerifyGetKey=createRemoteJWKSet(new URL(`https://api.workos.com/sso/jwks/${LEGACY_SOURCE.clientId}`),{timeoutDuration:5000})) {
  httpsUrl(issuer);
  return async(token:string):Promise<string|null>=>{
    if(token.length>16_384) return null;
    try {
      const {payload}=await jwtVerify(token,keySet,{issuer,algorithms:['RS256'],requiredClaims:['iss','sub','exp','iat','sid','client_id']});
      if(payload.client_id!==LEGACY_SOURCE.clientId||!payload.sub?.startsWith('user_')||typeof payload.sid!=='string'||!payload.sid.startsWith('session_')||payload.act) return null;
      return payload.sub;
    } catch{return null;}
  };
}

/** Only this authenticated boundary mints production proofs. No email/UUID body input. */
export class LegacyRecovery {
  constructor(private auth:NeonAuth,private verifyLegacy:(token:string)=>Promise<string|null>,private identities:Pick<IdentityStore,'resolveIdentity'>,private linker:Pick<IdentityLinker,'linkIdentity'>,private getPool:()=>Pool) {}
  async link(request:Request,legacyAccessToken:string):Promise<{userId:string}> {
    const current=await this.auth.authenticateRequest(request);
    if(!current) throw new ApiError(401,'unauthenticated','Sign in to continue.');
    const oldSubject=await this.verifyLegacy(legacyAccessToken);
    if(!oldSubject) throw new ApiError(409,'recovery_required','Verify your existing account before continuing.');
    const internalUserId=await this.identities.resolveIdentity(legacyAuthority,oldSubject);
    if(!internalUserId) throw new ApiError(409,'recovery_required','Your existing account needs migration verification.');
    const proofId=randomUUID();
    await this.getPool().query(`INSERT INTO identity_link_proofs
      (id,identity_id,issuer,subject,verification_method,expires_at)
      VALUES ($1,$2,$3,$4,'legacy_session',clock_timestamp()+interval '5 minutes')`,
      [proofId,internalUserId,current.issuer,current.subject]);
    try {
      const userId=await this.linker.linkIdentity({proofId,internalUserId,issuer:current.issuer,subject:current.subject});
      return {userId};
    } catch(error) {
      if(error instanceof IdentityError) throw new ApiError(409,'recovery_required','Your existing account needs migration verification.');
      throw error;
    }
  }
}
