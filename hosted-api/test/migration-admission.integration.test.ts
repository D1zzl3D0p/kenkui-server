import { describe, expect, it, vi } from 'vitest';
import { randomUUID } from 'node:crypto';
import { MigrationChallenges } from '../src/auth/migration-challenge.js';
import { Admission } from '../src/auth/admission.js';
import { NotificationStore } from '../src/db/notifications.js';
import { fixture, issuer } from './helpers/migration-fixture.js';

describe.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('explicit onboarding',()=>{
 it('serializes retries and writes only the first verified address with the binding',async()=>{
   const f=await fixture();
   try {
     const service=new Admission(()=>f.pool,'staging',issuer);
     const who=await f.profile('source@new.test');
     const [first,retry]=await Promise.all([service.onboard(who),service.onboard(who)]);
     expect(first).toEqual(retry);
     const store=new NotificationStore(()=>f.pool);
     expect((await store.settings(first.userId)).email).toBe('source@new.test');
     await store.setEnabled(first.userId,false);
     await f.pool.query('UPDATE neon_auth."user" SET email=$1 WHERE id=$2',['changed@new.test',who.subject]);
     expect(await service.onboard(who)).toEqual(first);
     expect(await store.settings(first.userId)).toEqual({email:'source@new.test',emailOnCompletion:false});
     for(const denied of [await f.profile('Legacy@old.test'),await f.profile('alias@old.test'),
       await f.profile('unverified@new.test',false),await f.profile('blocked@new.test',true,true)]){
       await expect(service.onboard(denied)).rejects.toMatchObject({status:409});
     }
     expect((await f.pool.query('SELECT count(*)::int n FROM identities')).rows[0].n).toBe(2);
     expect((await f.pool.query('SELECT count(*)::int n FROM provider_identities')).rows[0].n).toBe(2);
   } finally {await f.close();}
 });
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('rejects ambiguous or restored-only addresses, unknown policy, stale directory and absent current profile',async()=>{
 const f=await fixture();
 try {
   const service=new Admission(()=>f.pool,'staging',issuer);
   const person=await f.profile('different@new.test');
   const active=(await f.pool.query('SELECT run_id FROM migration_directory_active')).rows[0].run_id;
   await f.pool.query('UPDATE migration_directory_runs SET invited_emails=$1', [['invited@test.invalid']]);
   await expect(service.onboard(person)).rejects.toMatchObject({status:409});
   const invited=await f.profile('Invited@Test.Invalid');
   expect((await service.onboard(invited)).userId).toBeTruthy();
   await f.pool.query("UPDATE migration_directory_runs SET invited_emails='{}'");
   await f.pool.query("UPDATE migration_directory_rows SET address_key='legacy@old.test' WHERE owner_id IS NULL");
   const collision=await f.profile('legacy@old.test');
   await expect(service.onboard(collision)).rejects.toMatchObject({status:409});
   await expect(service.onboard({...person,subject:randomUUID()})).rejects.toMatchObject({status:409});
   await f.pool.query("UPDATE migration_directory_runs SET valid_until=clock_timestamp()-interval '1 second'");
   await expect(service.onboard(person)).rejects.toMatchObject({status:503});
   await f.pool.query("DELETE FROM migration_directory_active WHERE run_id=$1",[active]);
   await expect(service.onboard(person)).rejects.toMatchObject({status:503});
 } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('serializes simultaneous explicit create and verified link without orphan owners or reassignment',async()=>{
 const f=await fixture();
 let code='';
 const service=new Admission(()=>f.pool,'staging',issuer);
 const challenges=new MigrationChallenges(()=>f.pool,'staging',issuer,
   ()=>({ready:async()=>{},deliver:async(_id:string,value:string)=>{code=value;}}),()=>0,async()=>{});
 try {
   const who=await f.profile('fresh@new.test');
   const {challengeId}=await challenges.issue(who,'legacy@old.test');
   const results=await Promise.allSettled([service.onboard(who),challenges.verify(who,challengeId,code)]);
   const binding=(await f.pool.query('SELECT identity_id FROM provider_identities WHERE issuer=$1 AND subject=$2',[issuer,who.subject])).rows;
   expect(binding).toHaveLength(1);
   for(const result of results) {
     if(result.status==='fulfilled') expect(result.value.userId).toBe(binding[0].identity_id);
     else expect(result.reason).toMatchObject({status:409});
   }
   const orphans=await f.pool.query('SELECT id FROM identities i WHERE NOT EXISTS(SELECT 1 FROM provider_identities p WHERE p.identity_id=i.id)');
   expect(orphans.rows).toEqual([]);
   expect((await f.pool.query('SELECT count(*)::int n FROM credit_accounts')).rows[0].n).toBe(1);
 } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('reads the committed replacement directory after waiting for the exclusive activation guard',async()=>{
 const f=await fixture();
 const service=new Admission(()=>f.pool,'staging',issuer);
 const conn=await f.pool.connect();
 try {
   const who=await f.profile();
   await conn.query('BEGIN');
   await conn.query("SELECT pg_advisory_xact_lock(915001,hashtext('staging'))");
   const pending=service.onboard(who).then(()=>0,(error:{status:number})=>error.status);
   for(let i=0;i<100;i++){
     const waiting=await conn.query("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=915001 AND NOT granted AND database=(SELECT oid FROM pg_database WHERE datname=current_database())");
     if(waiting.rowCount) break;
     if(i===99) throw Error('missing shared directory guard');
     await new Promise(resolve=>setTimeout(resolve,5));
   }
   await conn.query("UPDATE migration_directory_runs SET invited_emails=ARRAY['allowed@new.test']");
   await conn.query('COMMIT');
   expect(await pending).toBe(409);
   expect((await f.pool.query('SELECT count(*)::int n FROM identities')).rows[0].n).toBe(1);
 } finally {await conn.query('ROLLBACK');conn.release();await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('keeps the admission verified address stable through the binding write',async()=>{
 const f=await fixture();
 try {
   const who=await f.profile();
   await f.pool.query(`CREATE FUNCTION revoke_email() RETURNS trigger LANGUAGE plpgsql AS $$
     BEGIN UPDATE neon_auth."user" SET "emailVerified"=false; RETURN NEW; END $$`);
   await f.pool.query('CREATE TRIGGER revoke_email AFTER INSERT ON identities FOR EACH ROW EXECUTE FUNCTION revoke_email()');
   await expect(new Admission(()=>f.pool,'staging',issuer).onboard(who)).rejects.toMatchObject({status:409});
   expect((await f.pool.query('SELECT count(*)::int n FROM identities')).rows[0].n).toBe(1);
 } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('rejects token expiry during preflight as unauthenticated',async()=>{
 const f=await fixture();
 try {
   const who=await f.profile();
   who.expiresAt=Date.now()/1000+0.1;
   const service=new MigrationChallenges(()=>f.pool,'staging',issuer,
     ()=>({ready:async()=>{await new Promise(r=>setTimeout(r,150));},deliver:async()=>{throw Error();}}),()=>0,async()=>{});
   await expect(service.issue(who,'legacy@old.test')).rejects.toMatchObject({status:401});
   expect((await f.pool.query('SELECT count(*)::int n FROM migration_rate_events')).rows[0].n).toBe(0);
 } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('rejects a credential expiring behind the directory guard before binding',async()=>{
 const f=await fixture();
 const conn=await f.pool.connect();
 const clock=vi.spyOn(Date,'now');
 try {
   const who=await f.profile();
   await conn.query('BEGIN');
   await conn.query("SELECT pg_advisory_xact_lock(915001,hashtext('staging'))");
   const pending=new Admission(()=>f.pool,'staging',issuer).onboard(who)
     .then(()=>0,(error:{status:number})=>error.status);
   await expect.poll(async()=>Number((await conn.query(`SELECT count(*) n FROM pg_locks
     WHERE locktype='advisory' AND classid=915001 AND NOT granted
       AND database=(SELECT oid FROM pg_database WHERE datname=current_database())`)).rows[0].n)).toBe(1);
   clock.mockReturnValue(1000*who.expiresAt);
   await conn.query('COMMIT');
   expect(await pending).toBe(401);
   expect((await f.pool.query('SELECT count(*)::int n FROM identities')).rows[0].n).toBe(1);
 } finally {clock.mockRestore();await conn.query('ROLLBACK');conn.release();await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('admits a source-only address to a fresh UUID beside a quarantine',async()=>{
 const f=await fixture();
 try {
   await f.python('quarantine');
   const q=(await f.pool.query('SELECT owner_id FROM migration_directory_quarantines')).rows[0];
   const service=new Admission(()=>f.pool,'staging',issuer);
   const profile=await f.profile('source@new.test');
   expect((await service.onboard(profile)).userId).not.toBe(q.owner_id);
   expect((await f.pool.query('SELECT subject FROM provider_identities WHERE identity_id=$1',[q.owner_id])).rows)
     .toEqual([{subject:'user_fixture'}]);
 } finally {await f.close();}
});
