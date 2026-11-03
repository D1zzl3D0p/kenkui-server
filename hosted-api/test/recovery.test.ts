import { beforeAll,describe,expect,it,vi } from 'vitest';
import { createLocalJWKSet,exportJWK,generateKeyPair,SignJWT } from 'jose';
import { createLegacyVerifier, LegacyRecovery, LEGACY_SOURCE } from '../src/auth/recovery.js';
import type { NeonAuth } from '../src/auth/neon.js';

let keys:Awaited<ReturnType<typeof generateKeyPair>>;let keySet:ReturnType<typeof createLocalJWKSet>;
const legacyIssuer='https://api.workos.com/user_management/'+LEGACY_SOURCE.clientId;
beforeAll(async()=>{keys=await generateKeyPair('RS256');keySet=createLocalJWKSet({keys:[{...await exportJWK(keys.publicKey),kid:'legacy'}]});});
async function legacy(claims:Record<string,unknown>={}) {return new SignJWT({iss:legacyIssuer,sub:'user_verified',client_id:LEGACY_SOURCE.clientId,sid:'session_verified',exp:Math.floor(Date.now()/1000)+300,iat:Math.floor(Date.now()/1000),...claims}).setProtectedHeader({alg:'RS256',kid:'legacy'}).sign(keys.privateKey);}
describe('migration proof issuance',()=>{
 it('verifies exact legacy issuer/client/signature/purpose; never accepts email or sealed credentials',async()=>{
   const verify=createLegacyVerifier(legacyIssuer,keySet);
   expect(await verify(await legacy())).toBe('user_verified');
   for(const claims of [{iss:'https://api.workos.com'},{client_id:'wrong'},{sid:undefined},{sub:'agent_123'},{act:{sub:'admin'}},{exp:1}]) expect(await verify(await legacy(claims))).toBeNull();
   expect(await verify('Fe26.2*sealed')).toBeNull();
 });
 it('reports an unavailable legacy signing-key service as a dependency failure',async()=>{
   vi.stubGlobal('fetch',async()=>new Response('private content',{status:503}));
   try {
     await expect(createLegacyVerifier(legacyIssuer)(await legacy())).rejects.toMatchObject({
       status:503,message:'Authentication is temporarily unavailable.',
     });
   } finally {vi.unstubAllGlobals();}
 });
 it('does not acquire a database connection until both credentials verify',async()=>{
   const pool=vi.fn(()=>{throw Error('database should be idle');});
   const auth={authenticateRequest:vi.fn(async()=>null)} as unknown as NeonAuth;
   const recovery=new LegacyRecovery(auth,createLegacyVerifier(legacyIssuer,keySet),pool,'staging','https://neon.invalid/db/auth');
   const request=new Request('https://api.invalid');
   await expect(recovery.link(request,await legacy())).rejects.toMatchObject({code:'unauthenticated'});
   vi.mocked(auth.authenticateRequest).mockResolvedValue({issuer:'https://neon.invalid/db/auth',subject:'new-user',expiresAt:9999999999,kind:'browser'});
   await expect(recovery.link(request,'fake')).rejects.toMatchObject({code:'recovery_required'});
   expect(pool).not.toHaveBeenCalled();
 });
});
