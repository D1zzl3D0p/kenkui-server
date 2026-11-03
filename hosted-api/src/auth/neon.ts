import { createRemoteJWKSet, decodeJwt, jwtVerify, type JWTVerifyGetKey } from 'jose';
import { ApiError, type AuthVerifier, type Principal } from '../types.js';
import type { IdentityStore } from '../db/identities.js';

export interface NeonAuthConfig {
  issuer:string; jwksUrl:string; resource:string; nativeClientId:string; allowedOrigins:readonly string[];
}
export interface ProviderIdentity { issuer:string; subject:string }
export interface VerifiedToken extends ProviderIdentity { expiresAt:number; kind:'browser'|'native' }
export interface NeonAuth extends AuthVerifier {
  authenticate(token:string):Promise<VerifiedToken|null>;
  authenticateRequest(request:Request):Promise<VerifiedToken|null>;
}

export function httpsUrl(value:string):URL {
  const url=new URL(value);
  if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash) throw new Error('Invalid auth configuration');
  return url;
}

/** One instance per API isolate. JWKS refresh is request-driven; no startup fetch/timer. */
export function createNeonAuth(config:NeonAuthConfig, identities:Pick<IdentityStore,'resolveIdentity'>, options:{keySet?:JWTVerifyGetKey}={}):NeonAuth {
  const origin=httpsUrl(config.issuer).origin;
  if(config.issuer.endsWith('/')||config.issuer===origin||!config.nativeClientId) throw new Error('Invalid auth configuration');
  httpsUrl(config.resource); httpsUrl(config.jwksUrl);
  const keySet=options.keySet??createRemoteJWKSet(new URL(config.jwksUrl),{cacheMaxAge:600_000,cooldownDuration:30_000,timeoutDuration:5_000});
  async function authenticate(token:string):Promise<VerifiedToken|null> {
    if(token.length>16_384) return null;
    try {
      // Unverified issuer selects a strict verification contract; it never authorizes.
      const claimedIssuer=decodeJwt(token).iss;
      if(claimedIssuer!==config.issuer&&claimedIssuer!==origin) return null;
      const native=claimedIssuer===config.issuer;
      const {payload}=await jwtVerify(token,keySet,{algorithms:['EdDSA'],issuer:native?config.issuer:origin,audience:native?config.resource:origin,requiredClaims:['iss','aud','exp','sub','iat']});
      if(!payload.sub?.trim()||typeof payload.exp!=='number'||payload.isAnonymous===true||payload.banned===true) return null;
      if(native ? payload.client_id!==config.nativeClientId : payload.role!=='authenticated') return null;
      // Both token families address neon_auth.user.id within this exact branch.
      // Prove actual subject parity in staging before cutover; never merge by email.
      return {issuer:config.issuer,subject:payload.sub,expiresAt:payload.exp,kind:native?'native':'browser'};
    } catch {return null;}
  }
  async function authenticateRequest(request:Request):Promise<VerifiedToken|null> {
    const authorization=request.headers.get('authorization');
    if(!authorization) {
      if(/(?:^|;\s*)__Host-kenkui-session=/.test(request.headers.get('cookie')??'')) throw new ApiError(401,'login_required','Sign in again to continue.');
      return null;
    }
    const match=/^Bearer ([^\s]+)$/i.exec(authorization);
    if(!match) return null;
    const token=match[1]!;
    if(token.split('.').length!==3) throw new ApiError(401,'login_required','Sign in again to continue.');
    const verified=await authenticate(token);
    if(!verified) return null;
    // The API accepts only explicit Bearer credentials, never managed cookies.
    const originHeader=request.headers.get('origin');
    if(!['GET','HEAD','OPTIONS'].includes(request.method)&&originHeader&&!config.allowedOrigins.includes(originHeader)) throw new ApiError(403,'invalid_origin','Request origin is not allowed.');
    return verified;
  }
  return {authenticate,authenticateRequest,async verify(request):Promise<Principal|null> {
    const verified=await authenticateRequest(request);if(!verified) return null;
    const identity={issuer:verified.issuer,subject:verified.subject};
    const userId=await identities.resolveIdentity(identity.issuer,identity.subject);
    if(!userId) throw new ApiError(409,'recovery_required','Verify your existing account before continuing.');
    return {...identity,userId};
  }};
}

/** Missing setup leaves auth unavailable without breaking static catalogs. */
export function loadNeonAuthConfig(env:NodeJS.ProcessEnv,allowedOrigins:readonly string[]):NeonAuthConfig|undefined {
  const values=[env.NEON_AUTH_BASE_URL,env.NEON_AUTH_JWKS_URL,env.KENKUI_AUTH_RESOURCE,env.KENKUI_NATIVE_CLIENT_ID];
  if(values.every(v=>!v)) return undefined;
  if(values.some(v=>!v)) throw new Error('Incomplete auth configuration');
  return {issuer:values[0]!,jwksUrl:values[1]!,resource:values[2]!,nativeClientId:values[3]!,allowedOrigins};
}
