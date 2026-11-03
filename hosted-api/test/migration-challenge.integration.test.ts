import { randomUUID } from 'node:crypto';
import { expect, it } from 'vitest';
import { MigrationChallenges } from '../src/auth/migration-challenge.js';
import { fixture,issuer,owner } from './helpers/migration-fixture.js';

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('commits limits, authenticates challenges against current profile, consumes once with the linker',async()=>{
  const f=await fixture();
  const sent:{id:string;code:string}[]=[];
  const transport={ready:async()=>{},deliver:async(id:string,code:string)=>{sent.push({id,code});}};
  const service=new MigrationChallenges(()=>f.pool,'staging',issuer,()=>transport,()=>0,async()=>{});
  try {
    const who=await f.profile('private@new.test',false);
    await expect(service.issue({...who,subject:randomUUID()},'legacy@old.test')).rejects.toMatchObject({status:409});
    await expect(service.issue(await f.profile('banned@test.invalid',true,true),'legacy@old.test')).rejects.toMatchObject({status:409});
    const other=await f.profile();
    const challenge=await service.issue(who,'legacy@old.test');
    expect(sent).toEqual([{id:challenge.challengeId,code:expect.stringMatching(/^[0-9A-HJKMNP-TV-Z]{16}$/)}]);
    const delivery=await f.python('deliver',challenge.challengeId,sent[0]!.code);
    expect(JSON.parse(delivery.stdout)).toEqual({statuses:[200,409],messages:1});
    await expect(service.issue(who,'unknown@old.test')).rejects.toMatchObject({status:429});
    await expect(service.verify(other,challenge.challengeId,'0000000000000000')).rejects.toMatchObject({status:409});
    await expect(service.verify(who,challenge.challengeId,'0000000000000000')).rejects.toMatchObject({status:409});
    const state=async()=>(await f.pool.query('SELECT attempts,consumed_at FROM migration_challenges WHERE id=$1',[challenge.challengeId])).rows[0];
    expect(await state()).toEqual({attempts:1,consumed_at:null});
    await f.pool.query('UPDATE neon_auth."user" SET banned=true WHERE id=$1',[who.subject]);
    await expect(service.verify(who,challenge.challengeId,sent[0]!.code)).rejects.toMatchObject({status:409});
    await f.pool.query('UPDATE neon_auth."user" SET banned=false WHERE id=$1',[who.subject]);
    const results=await Promise.allSettled([1,2].map(()=>service.verify(who,challenge.challengeId,sent[0]!.code.toLowerCase())));
    expect(results.filter(x=>x.status==='fulfilled')).toEqual([{status:'fulfilled',value:{userId:owner}}]);
    expect((await f.pool.query('SELECT id,consumed_at FROM identity_link_proofs')).rows).toEqual([{id:expect.not.stringMatching(challenge.challengeId),consumed_at:expect.any(Date)}]);
    await service.issue(other,'alias@old.test');
    expect(sent).toHaveLength(1);
    await expect(service.verify(other,randomUUID(),'0000000000000000')).rejects.toMatchObject({status:409});
  } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('suppresses owner limits across callers, expires superseded and old-run codes, and pads all 202 paths',async()=>{
  const f=await fixture();
  const sent:{id:string;code:string}[]=[];
  const sleeps:number[]=[];
  let elapsed=0;
  let ready=true;
  let readinessChecks=0;
  const mail={ready:async()=>{readinessChecks++;if(!ready)throw Error();},deliver:async(id:string,code:string)=>{
    sent.push({id,code});elapsed+=1500;throw Error('uncertain delivery');
  }};
  const service=new MigrationChallenges(()=>f.pool,'staging',issuer,()=>mail,()=>elapsed,async(ms:number)=>{sleeps.push(ms);elapsed+=ms;});
  const age=async()=>{await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '61 seconds'");};
  try {
    const who=await f.profile();
    const other=await f.profile();
    const first=await service.issue(who,'legacy@old.test');
    await service.issue(other,'legacy@old.test');
    await age();
    const second=await service.issue(who,'legacy@old.test');
    expect(sleeps).toEqual([500,2000,500]);
    expect(sent.map(c=>c.id)).toEqual([first.challengeId,second.challengeId]);
    await expect(service.verify(who,first.challengeId,sent[0]!.code)).rejects.toMatchObject({status:409});
    const ttl=(await f.pool.query('SELECT extract(epoch from expires_at-created_at)::float8 n FROM migration_challenges WHERE id=$1',[second.challengeId])).rows[0].n;
    expect(ttl).toBeCloseTo(600,1);
    await f.python('activate');
    expect(JSON.parse((await f.python('deliver',second.challengeId,sent[1]!.code)).stdout)).toEqual({statuses:[409,409],messages:0});
    await expect(service.verify(who,second.challengeId,sent[1]!.code)).rejects.toMatchObject({status:409});
    await age();
    await service.issue(who,'miss@old.test');
    await age();
    await expect(service.issue(who,'miss@old.test')).rejects.toMatchObject({status:429});
    await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '2 hours'");
    for(let i=0;i<7;i++) {
      await service.issue(who,'miss@old.test');
      await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '70 minutes'");
    }
    await expect(service.issue(who,'miss@old.test')).rejects.toMatchObject({status:429});
    ready=false;
    for(const hint of ['legacy@old.test','miss@old.test']) await expect(service.issue(who,hint)).rejects.toMatchObject({status:503});
    expect(readinessChecks).toBe(15);
  } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('enforces five rightful guesses, caller verification budget and expiry, including unknown challenge IDs',async()=>{
  const f=await fixture();
  let code='';
  const mail={ready:async()=>{},deliver:async(_id:string,value:string)=>{code=value;}};
  const service=new MigrationChallenges(()=>f.pool,'staging',issuer,()=>mail,()=>0,async()=>{});
  try {
    const who=await f.profile(),other=await f.profile();
    const {challengeId}=await service.issue(who,'legacy@old.test');
    for(let n=0;n<60;n++) await expect(service.verify(other,n%2?randomUUID():challengeId,code)).rejects.toMatchObject({status:409});
    await expect(service.verify(other,challengeId,code)).rejects.toMatchObject({status:429});
    for(let n=0;n<5;n++) await expect(service.verify(who,challengeId,'OOOOOOOOOOOOOOOO')).rejects.toMatchObject({status:409});
    await expect(service.verify(who,challengeId,code)).rejects.toMatchObject({status:409});
    expect((await f.pool.query('SELECT attempts FROM migration_challenges')).rows[0].attempts).toBe(5);
    await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '65 seconds'");
    const next=await service.issue(who,'legacy@old.test');
    await f.pool.query("UPDATE migration_challenges SET expires_at=clock_timestamp()-interval '1 second' WHERE id=$1",[next.challengeId]);
    await expect(service.verify(who,next.challengeId,code)).rejects.toMatchObject({status:409});
  } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('rolls back a failure after binding and proof consumption, preserving the usable challenge',async()=>{
  const f=await fixture();
  let code='';
  const mail={ready:async()=>{},deliver:async(_id:string,value:string)=>{code=value;}};
  const service=new MigrationChallenges(()=>f.pool,'staging',issuer,()=>mail,()=>0,async()=>{});
  try {
    const who=await f.profile();
    const {challengeId}=await service.issue(who,'legacy@old.test');
    await f.pool.query("ALTER TABLE migration_challenges ADD CHECK(consumed_at IS NULL)");
    await expect(service.verify(who,challengeId,code)).rejects.toMatchObject({status:503});
    expect((await f.pool.query('SELECT count(*)::int n FROM identity_link_proofs')).rows[0].n).toBe(0);
    expect((await f.pool.query('SELECT count(*)::int n FROM provider_identities WHERE issuer=$1',[issuer])).rows[0].n).toBe(0);
    expect((await f.pool.query('SELECT consumed_at FROM migration_challenges')).rows[0].consumed_at).toBeNull();
    await f.pool.query("ALTER TABLE migration_challenges DROP CONSTRAINT migration_challenges_consumed_at_check");
    expect(await service.verify(who,challengeId,code.slice(0,4)+' -'+code.slice(4))).toEqual({userId:owner});
  } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('shares a fixed digest vector and keeps claims single-use through SMTP failure and an HTTP deadline',async()=>{
 const f=await fixture();
 let code='';
 const service=new MigrationChallenges(()=>f.pool,'staging',issuer,
   ()=>({ready:async()=>{},deliver:async(_id:string,value:string)=>{code=value;}}),()=>0,async()=>{});
 try {
   const who=await f.profile();
   const {challengeId}=await service.issue(who,'legacy@old.test');
   const fixed='11111111-1111-4111-8111-111111111111';
   await f.pool.query("UPDATE migration_challenges SET id=$1,digest=decode($2,'hex') WHERE id=$3",
     [fixed,'fd31d5088b4b2d1829ebf9cbc77d8faf0a939e6132039696ee0a758181ba0329',challengeId]);
   expect(JSON.parse((await f.python('deliver',fixed,'0000000000000000')).stdout)).toEqual({messages:0,statuses:[409,409]});
   expect(JSON.parse((await f.python('fail_send',fixed,'0123456789ABCDEF')).stdout)).toEqual({messages:0,statuses:[409,503]});
   expect(JSON.parse((await f.python('deliver',fixed,'0123456789ABCDEF')).stdout)).toEqual({messages:0,statuses:[409,409]});
   expect(await service.verify(who,fixed,'0123456789ABCDEF')).toEqual({userId:owner});
   await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '61 seconds'");
   const next=await service.issue(who,'legacy@old.test');
   expect(JSON.parse((await f.python('slow_send',next.challengeId,code)).stdout)).toEqual({messages:1,statuses:[409,504]});
   expect(JSON.parse((await f.python('deliver',next.challengeId,code)).stdout)).toEqual({messages:0,statuses:[409,409]});
   expect(await service.verify(who,next.challengeId,code)).toEqual({userId:owner});
 } finally {await f.close();}
});

it.skipIf(!process.env.KENKUI_TEST_DATABASE_URL)('suppresses per-owner hourly and daily limits across independent new subjects',async()=>{
 const f=await fixture();
 const sent:string[]=[];
 const service=new MigrationChallenges(()=>f.pool,'staging',issuer,
   ()=>({ready:async()=>{},deliver:async(id:string)=>{sent.push(id);}}),()=>0,async()=>{});
 try {
   for(let i=0;i<4;i++){
     await service.issue(await f.profile(),'legacy@old.test');
     await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '61 seconds'");
   }
   expect(sent).toHaveLength(3);
   for(let i=0;i<8;i++){
     await f.pool.query("UPDATE migration_rate_events SET created_at=created_at-interval '1 hour'");
     await service.issue(await f.profile(),'legacy@old.test');
   }
   expect(sent).toHaveLength(10);
 } finally {await f.close();}
});
