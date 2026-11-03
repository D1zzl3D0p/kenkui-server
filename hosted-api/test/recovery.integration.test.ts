import { fixture } from './helpers/migration-fixture.js';
import { createLocalJWKSet,exportJWK,generateKeyPair,SignJWT } from 'jose';
import { describe,expect,it } from 'vitest';
import { IdentityStore } from '../src/db/identities.js';
import { createNeonAuth } from '../src/auth/neon.js';
import { createLegacyVerifier,LEGACY_SOURCE,LegacyRecovery } from '../src/auth/recovery.js';

const testUrl=process.env.KENKUI_TEST_DATABASE_URL;
describe.skipIf(!testUrl)('authenticated migration against real PostgreSQL',()=>{
 it('independently links Google/GitHub identities to old UUID/credits only after legacy verification',async()=>{
   expect(testUrl).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
   const f=await fixture();
   const {pool}=f;
   try {
     const store=new IdentityStore(()=>pool);
     const before={identities:(await pool.query('SELECT * FROM identities')).rows,credits:(await pool.query('SELECT * FROM credit_accounts')).rows,jobs:(await pool.query('SELECT * FROM jobs')).rows};
     const neonKeys=await generateKeyPair('EdDSA'),legacyKeys=await generateKeyPair('RS256');
     const issuer='https://branch.invalid/db/auth',origin=new URL(issuer).origin;
     const auth=createNeonAuth({issuer,jwksUrl:issuer+'/.well-known/jwks.json',resource:'https://api.invalid',nativeClientId:'desktop',allowedOrigins:[]},store,{keySet:createLocalJWKSet({keys:[{...await exportJWK(neonKeys.publicKey),kid:'neon'}]})});
     const legacyIssuer='https://api.workos.com/user_management/'+LEGACY_SOURCE.clientId;
     const verifier=createLegacyVerifier(legacyIssuer,createLocalJWKSet({keys:[{...await exportJWK(legacyKeys.publicKey),kid:'legacy'}]}));
     const recovery=new LegacyRecovery(auth,verifier,()=>pool,'staging',issuer);
     const legacy=await new SignJWT({iss:legacyIssuer,sub:'user_old',client_id:LEGACY_SOURCE.clientId,sid:'session_verified'}).setIssuedAt().setExpirationTime('5m').setProtectedHeader({alg:'RS256',kid:'legacy'}).sign(legacyKeys.privateKey);
     for(const subject of [(await f.profile()).subject,(await f.profile('private@relay.test',false)).subject]) {
       const jwt=await new SignJWT({iss:origin,aud:origin,sub:subject,role:'authenticated'}).setIssuedAt().setExpirationTime('5m').setProtectedHeader({alg:'EdDSA',kid:'neon'}).sign(neonKeys.privateKey);
       const request=new Request('https://api.invalid',{headers:{Authorization:'Bearer '+jwt}});
       await expect(auth.verify(request)).rejects.toMatchObject({code:'recovery_required'});
       await expect(recovery.link(request,'unverified')).rejects.toMatchObject({code:'recovery_required'});
       expect(await store.resolveIdentity(issuer,subject)).toBeNull();
       expect(await recovery.link(request,legacy)).toEqual({userId:'11111111-1111-4111-8111-111111111111'});
       expect((await auth.verify(request))?.userId).toBe('11111111-1111-4111-8111-111111111111');
     }
     expect((await pool.query('SELECT verification_method, consumed_at IS NOT NULL AS consumed, expires_at-verified_at AS ttl FROM identity_link_proofs')).rows).toEqual([0,1].map(()=>({verification_method:'legacy_session',consumed:true,ttl:expect.objectContaining({minutes:5})})));
     expect({identities:(await pool.query('SELECT * FROM identities')).rows,credits:(await pool.query('SELECT * FROM credit_accounts')).rows,jobs:(await pool.query('SELECT * FROM jobs')).rows}).toEqual(before);
   } finally {await f.close();}
 });
});
