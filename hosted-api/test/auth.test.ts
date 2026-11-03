import { beforeAll, describe, expect, it, vi } from 'vitest';
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import { createNeonAuth } from '../src/auth/neon.js';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';

const issuer='https://branch.example.invalid/neondb/auth';
const origin=new URL(issuer).origin;
const config={issuer,jwksUrl:issuer+'/.well-known/jwks.json',resource:'https://api.example.invalid',nativeClientId:'desktop',allowedOrigins:['https://app.example.invalid']};
let keys:Awaited<ReturnType<typeof generateKeyPair>>;
let keySet:ReturnType<typeof createLocalJWKSet>;
beforeAll(async()=>{keys=await generateKeyPair('EdDSA'); keySet=createLocalJWKSet({keys:[{...await exportJWK(keys.publicKey),kid:'one'}]});});
async function token(claims:Record<string,unknown>={},native=false,signingKey?:CryptoKey) {
  return new SignJWT({sub:'neon-user',iss:native?issuer:origin,aud:native?config.resource:origin,exp:Math.floor(Date.now()/1000)+300,iat:Math.floor(Date.now()/1000),...(native?{client_id:'desktop'}:{role:'authenticated'}),...claims}).setProtectedHeader({alg:'EdDSA',kid:'one'}).sign(signingKey??keys.privateKey);
}
const req=(token:string)=>new Request('https://api.example.invalid/v1/auth/session',{headers:{Authorization:`Bearer ${token}`}});
function setup() {
 const identities={resolveIdentity:vi.fn(async()=> '00000000-0000-0000-0000-000000000001' as string|null),createNewIdentity:vi.fn(async()=> 'new-owner')};
 return {identities,auth:createNeonAuth(config,identities,{keySet})};
}
describe('Neon authentication boundary',()=>{
 it('loads JWKS only on demand, shares cache and never refreshes on an idle timer',async()=>{
   const fetcher=vi.fn(async()=>Response.json({keys:[{...await exportJWK(keys.publicKey),kid:'one'}]}));
   vi.stubGlobal('fetch',fetcher);vi.useFakeTimers();
   try {
     const auth=createNeonAuth(config,{resolveIdentity:async()=> 'owner'});
     expect(fetcher).not.toHaveBeenCalled();
     const jwt=await token();await Promise.all([auth.verify(req(jwt)),auth.verify(req(jwt))]);expect(fetcher).toHaveBeenCalledOnce();
     await vi.advanceTimersByTimeAsync(3_600_000);expect(fetcher).toHaveBeenCalledOnce();
     expect(await auth.verify(req(await token()))).not.toBeNull();expect(fetcher).toHaveBeenCalledTimes(2);
   } finally{vi.useRealTimers();vi.unstubAllGlobals();}
 });
 it('maps browser and native signatures to the same deliberate provider authority',async()=>{
   const {auth,identities}=setup();
   for(const native of [false,true]) expect(await auth.verify(req(await token({},native)))).toEqual({userId:'00000000-0000-0000-0000-000000000001',issuer,subject:'neon-user'});
   expect(identities.resolveIdentity.mock.calls).toEqual([[issuer,'neon-user'],[issuer,'neon-user']]);
 });
 it.each([{iss:'https://other.invalid'},{aud:'wrong'},{exp:1},{exp:undefined},{sub:undefined},{sub:''},{role:'anonymous'},{isAnonymous:true},{banned:true}])('rejects invalid browser claims before repositories: %j',async claims=>{
   const {auth,identities}=setup(); expect(await auth.verify(req(await token(claims)))).toBeNull(); expect(identities.resolveIdentity).not.toHaveBeenCalled();
 });
 it('rejects signatures, native wrong branch/audience/client and browser/native claim mixing',async()=>{
   const {auth,identities}=setup(); const other=await generateKeyPair('EdDSA');
   expect(await auth.verify(req(await token({},false,other.privateKey)))).toBeNull();
   for(const claims of [{iss:issuer+'/other'},{aud:origin},{client_id:'other'},{client_id:undefined}]) expect(await auth.verify(req(await token(claims,true)))).toBeNull();
   expect(identities.resolveIdentity).not.toHaveBeenCalled();
 });
 it('accepts native resource audience membership',async()=>{
   const {auth}=setup(); expect(await auth.verify(req(await token({aud:[config.resource,issuer+'/oauth2/userinfo']},true)))).not.toBeNull();
 });
 it('requires one-time login for opaque WorkOS sessions; no cookie substitution',async()=>{
   const {auth,identities}=setup(); await expect(auth.verify(req('Fe26.2*sealed'))).rejects.toMatchObject({code:'login_required'});
   await expect(auth.verify(new Request('https://api.example.invalid',{headers:{cookie:'__Host-kenkui-session=old'}}))).rejects.toMatchObject({code:'login_required'});
   expect(identities.resolveIdentity).not.toHaveBeenCalled();
 });
 it('does not create an unknown account from HTTP assertions or email',async()=>{
   const {auth,identities}=setup(); identities.resolveIdentity.mockResolvedValue(null);
   await expect(auth.verify(req(await token({email:'old@example.invalid',emailVerified:true,userId:'chosen','legacyAccountCheck':'confirmed-new'})))).rejects.toMatchObject({code:'recovery_required'});
   expect(identities.createNewIdentity).not.toHaveBeenCalled();
 });
 it('keeps ordinary requests resolve-only after repeated misses',async()=>{
   const {auth,identities}=setup(); identities.resolveIdentity.mockResolvedValue(null);
   for(let i=0;i<2;i++) await expect(auth.verify(req(await token()))).rejects.toMatchObject({status:409});
   expect(identities.createNewIdentity).not.toHaveBeenCalled();
 });
 it('bad credentials never reach a protected handler; browser mutations reject foreign Origin',async()=>{
   const {auth}=setup(); const getPool=vi.fn(()=>{throw new Error('DB forbidden');});
   const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),auth,getPool}); const handler=vi.fn(c=>c.json({ok:true})); app.get('/v1/jobs',handler);
   expect((await app.request('/v1/jobs',{headers:{Authorization:`Bearer ${await token({aud:'wrong'})}`}})).status).toBe(401); expect(handler).not.toHaveBeenCalled(); expect(getPool).not.toHaveBeenCalled();
   await expect(auth.verify(new Request('https://api.example.invalid/v1/auth/logout',{method:'POST',headers:{Authorization:`Bearer ${await token()}`,Origin:'https://evil.invalid'}}))).rejects.toMatchObject({code:'invalid_origin'});
 });
});

it('distinguishes unusable remote signing keys from an invalid signed credential',async()=>{
 const signed=await token();
 const repository={resolveIdentity:vi.fn(async()=>null)};
 const check=()=>createNeonAuth(config,repository).verify(req(signed));
 try {
   for(const fetcher of [async()=>new Response('private error',{status:500}),
     async()=>{throw new DOMException('private timeout','TimeoutError');},
     async()=>Response.json({keys:'private malformation'})]){
     vi.stubGlobal('fetch',fetcher);
     await expect(check()).rejects.toMatchObject({status:503,message:'Authentication is temporarily unavailable.'});
   }
   const wrong=await generateKeyPair('EdDSA');
   vi.stubGlobal('fetch',async()=>Response.json({keys:[{...await exportJWK(wrong.publicKey),kid:'one'}]}));
   expect(await check()).toBeNull();
   expect(repository.resolveIdentity).not.toHaveBeenCalled();
 } finally {vi.unstubAllGlobals();}
});
