import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from 'jose';
import { expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { createNeonAuth } from '../src/auth/neon.js';
import { registerAuthRoutes } from '../src/auth/pages.js';
import { ModalBridge } from '../src/compute/modal.js';
import { loadConfig } from '../src/config.js';
import { Admission } from '../src/auth/admission.js';
import { MigrationChallenges } from '../src/auth/migration-challenge.js';
import { fixture,owner,issuer } from './helpers/migration-fixture.js';

it('requires signed browser/native bearer, correct Origin and bounded exact input on explicit endpoints',async()=>{
 const pair=await generateKeyPair('EdDSA');
 const origin=new URL(issuer).origin;
 const config={issuer,jwksUrl:issuer+'/jwks',nativeClientId:'desktop',resource:'https://api.invalid',allowedOrigins:['https://app.invalid']};
 const auth=createNeonAuth(config,{resolveIdentity:async()=>null},
   {keySet:createLocalJWKSet({keys:[{...await exportJWK(pair.publicKey),kid:'test'}]})});
 const api=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>{throw Error();},auth});
 const account={userId:'synthetic'},challenge={challengeId:'11111111-1111-4111-8111-111111111111'};
 const migration={auth,admission:{onboard:async()=>account},challenges:{issue:async()=>challenge,verify:async()=>account}};
 registerAuthRoutes(api,config,{migration});
 for(const native of [false,true]){
   const jwt=await new SignJWT(native?{client_id:'desktop'}:{role:'authenticated'}).setSubject('subject')
     .setIssuer(native?issuer:origin).setAudience(native?config.resource:origin).setIssuedAt().setExpirationTime('2m')
     .setProtectedHeader({alg:'EdDSA',kid:'test'}).sign(pair.privateKey);
   for(const [path,body,status] of [['onboarding',{},200],['migration/challenge',{legacyEmail:'old@test.invalid'},202],
     ['migration/verify',{...challenge,code:'0123456789ABCDEF'},200]] as const){
     const post=(raw:string,token=jwt,site='https://app.invalid',type='application/json')=>api.request('/v1/auth/'+path,
       {method:'POST',headers:{Authorization:'Bearer '+token,Origin:site,'Content-Type':type},body:raw});
     const response=await post(JSON.stringify(body));
     expect(response.status).toBe(status);expect(response.headers.get('Cache-Control')).toBe('no-store');
     expect((await post(JSON.stringify({...body,email:'other@test.invalid'}))).status).toBe(422);
     expect((await post(' '.repeat(2100))).status).toBe(413);
     expect((await post('{}',jwt,'https://evil.invalid')).status).toBe(403);
     expect((await post('{}','bad.bad.bad')).status).toBe(401);
     expect((await post('{}',jwt,'https://app.invalid','text/plain')).status).toBe(415);
   }
 }
});

it('uses only fixed protected mail payloads and aborts a slow delivery after 1500 ms',async()=>{
 const calls:[string,RequestInit][]=[];
 const bridge=new ModalBridge({url:'https://test.modal.run',environment:'staging',key:'k',secret:'s',bridgeSecret:'b'},
   async(url,init)=>{calls.push([url,init]);return url.endsWith('/ready')?Response.json({ready:true}):new Promise(()=>{});});
 await bridge.ready();
 vi.useFakeTimers();
 try {
   const result=bridge.deliver('11111111-1111-4111-8111-111111111111','0123456789ABCDEF').catch(e=>e);
   await vi.advanceTimersByTimeAsync(1500);
   expect((await result).status).toBe(503);
   expect(calls.map(([url,init])=>[url,JSON.parse(String(init.body))])).toEqual([
     ['https://test.modal.run/migration-email/ready',{environment:'staging'}],
     ['https://test.modal.run/migration-email',{environment:'staging',challengeId:'11111111-1111-4111-8111-111111111111',code:'0123456789ABCDEF'}]]);
   expect(calls[1]![1].signal?.aborted).toBe(true);
 } finally {vi.useRealTimers();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('recovers with a signed browser token and explicitly admits a native subject against the migrated DB',async()=>{
 const f=await fixture();
 try {
   const pair=await generateKeyPair('EdDSA');
   const origin=new URL(issuer).origin;
   const config={issuer,jwksUrl:issuer+'/jwks',nativeClientId:'desktop',resource:'https://api.invalid',allowedOrigins:[]};
   const auth=createNeonAuth(config,{resolveIdentity:async(iss,sub)=>(await f.pool.query('SELECT identity_id FROM provider_identities WHERE issuer=$1 AND subject=$2',[iss,sub])).rows[0]?.identity_id??null},
     {keySet:createLocalJWKSet({keys:[{...await exportJWK(pair.publicKey),kid:'test'}]})});
   const api=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool:()=>f.pool,auth});
   let code='';
   registerAuthRoutes(api,config,{migration:{
     auth,admission:new Admission(()=>f.pool,'staging',issuer),
     challenges:new MigrationChallenges(()=>f.pool,'staging',issuer,
       ()=>({ready:async()=>{},deliver:async(_id:string,value:string)=>{code=value;}}),()=>0,async()=>{}),
   }});
   const post=(path:string,jwt:string,body:object)=>api.request('/v1/auth/'+path,{method:'POST',
     headers:{Authorization:'Bearer '+jwt,'Content-Type':'application/json'},body:JSON.stringify(body)});
   for(const native of [true,false]){
     const profile=await f.profile(native?'new@test.invalid':'private@relay.test',native);
     const jwt=await new SignJWT(native?{client_id:'desktop'}:{role:'authenticated'}).setSubject(profile.subject)
       .setIssuer(native?issuer:origin).setAudience(native?config.resource:origin).setIssuedAt().setExpirationTime('2m')
       .setProtectedHeader({alg:'EdDSA',kid:'test'}).sign(pair.privateKey);
     const session=()=>api.request('/v1/auth/session',{headers:{Authorization:'Bearer '+jwt}});
     expect((await session()).status).toBe(409);
     if(native){
       const created=await post('onboarding',jwt,{});
       expect(created.status).toBe(200);expect(await created.json()).toEqual(await (await session()).json());
     } else {
       const issue=await post('migration/challenge',jwt,{legacyEmail:'legacy@old.test'});
       expect(issue.status).toBe(202);
       const {challengeId}=await issue.json();
       const bad=jwt.slice(0,jwt.lastIndexOf('.')+1)+'invalid';
       expect((await post('migration/verify',bad,{challengeId,code})).status).toBe(401);
       expect((await post('migration/challenge',bad,{legacyEmail:'legacy@old.test'})).status).toBe(401);
       expect(await (await post('migration/verify',jwt,{challengeId,code})).json()).toEqual({userId:owner});
       expect(await (await session()).json()).toEqual({userId:owner});
     }
   }
 } finally {await f.close();}
});
