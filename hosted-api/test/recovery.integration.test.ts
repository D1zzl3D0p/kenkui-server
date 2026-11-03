import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { Client,Pool } from 'pg';
import { createLocalJWKSet,exportJWK,generateKeyPair,SignJWT } from 'jose';
import { describe,expect,it } from 'vitest';
import { IdentityStore } from '../src/db/identities.js';
import { IdentityLinker } from '../src/auth/linking.js';
import { createNeonAuth } from '../src/auth/neon.js';
import { createLegacyVerifier,LEGACY_SOURCE,LegacyRecovery } from '../src/auth/recovery.js';

const testUrl=process.env.KENKUI_TEST_DATABASE_URL;
describe.skipIf(!testUrl)('authenticated migration against real PostgreSQL',()=>{
 it('independently links Google/GitHub identities to old UUID/credits only after legacy verification',async()=>{
   expect(testUrl).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
   const schema='identity_test_auth_'+randomUUID().replaceAll('-','');const admin=new Client({connectionString:testUrl!,ssl:false});await admin.connect();
   const pool=new Pool({connectionString:testUrl!,ssl:false,options:`-c search_path=${schema}`});
   try {
     await admin.query(`CREATE SCHEMA ${schema}`);
     await promisify(execFile)('../.venv/bin/python',['test/helpers/identity_database.py',schema,'initialize'],{env:{...process.env,KENKUI_TEST_DATABASE_URL:testUrl!}});
     await pool.query("UPDATE identities SET workos_subject='user_legacy'");
     const store=new IdentityStore(()=>pool);await store.workosBackfill(LEGACY_SOURCE)({authority:LEGACY_SOURCE,subjects:['user_legacy'],dryRun:false});
     const before={identities:(await pool.query('SELECT * FROM identities')).rows,credits:(await pool.query('SELECT * FROM credit_accounts')).rows,jobs:(await pool.query('SELECT * FROM jobs')).rows};
     const neonKeys=await generateKeyPair('EdDSA'),legacyKeys=await generateKeyPair('RS256');
     const issuer='https://branch.invalid/db/auth',origin=new URL(issuer).origin;
     const auth=createNeonAuth({issuer,jwksUrl:issuer+'/.well-known/jwks.json',resource:'https://api.invalid',nativeClientId:'desktop',allowedOrigins:[]},store,{keySet:createLocalJWKSet({keys:[{...await exportJWK(neonKeys.publicKey),kid:'neon'}]})});
     const legacyIssuer='https://api.workos.com/user_management/'+LEGACY_SOURCE.clientId;
     const verifier=createLegacyVerifier(legacyIssuer,createLocalJWKSet({keys:[{...await exportJWK(legacyKeys.publicKey),kid:'legacy'}]}));
     const recovery=new LegacyRecovery(auth,verifier,store,new IdentityLinker(()=>pool),()=>pool);
     const legacy=await new SignJWT({iss:legacyIssuer,sub:'user_legacy',client_id:LEGACY_SOURCE.clientId,sid:'session_verified'}).setIssuedAt().setExpirationTime('5m').setProtectedHeader({alg:'RS256',kid:'legacy'}).sign(legacyKeys.privateKey);
     for(const subject of ['google-neon-user','github-private-email-user']) {
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
   } finally {await pool.end();await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`);await admin.end();}
 });
});
