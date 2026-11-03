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
 it('performs no DB write until BOTH identities and old owner are authenticated',async()=>{
   const query=vi.fn(async(_sql:string,_values?:unknown[])=>({rows:[]}));const pool=()=>({query}) as never;
   const auth={authenticateRequest:vi.fn(async()=>null)} as unknown as NeonAuth;
   const store={resolveIdentity:vi.fn(async()=>null)};const linker={linkIdentity:vi.fn(async()=> 'owner')};
   const recovery=new LegacyRecovery(auth,createLegacyVerifier(legacyIssuer,keySet),store,linker,pool);
   const request=new Request('https://api.invalid');await expect(recovery.link(request,await legacy())).rejects.toMatchObject({code:'unauthenticated'});expect(query).not.toHaveBeenCalled();
   vi.mocked(auth.authenticateRequest).mockResolvedValue({issuer:'https://neon.invalid/db/auth',subject:'new-user',expiresAt:9999999999,kind:'browser'});
   await expect(recovery.link(request,'fake')).rejects.toMatchObject({code:'recovery_required'});expect(store.resolveIdentity).not.toHaveBeenCalled();
   await expect(recovery.link(request,await legacy())).rejects.toMatchObject({code:'recovery_required'});expect(query).not.toHaveBeenCalled();
 });
 it('derives owner and proof values only from verified identities',async()=>{
   const query=vi.fn(async(_sql:string,_values?:unknown[])=>({rows:[]}));const auth={authenticateRequest:vi.fn(async()=>({issuer:'https://neon.invalid/db/auth',subject:'new-user',expiresAt:9999999999,kind:'browser'}))} as unknown as NeonAuth;
   const store={resolveIdentity:vi.fn(async()=> 'old-owner')};const linker={linkIdentity:vi.fn(async()=> 'old-owner')};
   const service=new LegacyRecovery(auth,createLegacyVerifier(legacyIssuer,keySet),store,linker,()=>({query}) as never);
   expect(await service.link(new Request('https://api.invalid'),await legacy())).toEqual({userId:'old-owner'});
   expect(store.resolveIdentity).toHaveBeenCalledWith(`urn:kenkui:workos:${LEGACY_SOURCE.environmentId}:${LEGACY_SOURCE.clientId}`,'user_verified');
   expect(query).toHaveBeenCalledOnce();expect(query.mock.calls[0]![1]).toEqual([expect.any(String),'old-owner','https://neon.invalid/db/auth','new-user']);
   expect(linker.linkIdentity).toHaveBeenCalledWith({internalUserId:'old-owner',issuer:'https://neon.invalid/db/auth',subject:'new-user',proofId:expect.any(String)});
 });
});
