import { expect,it } from 'vitest';
import type { Pool } from 'pg';
import { NeonAdmission } from '../src/auth/admission.js';

const issuer='https://auth.invalid/db/auth';
const who={issuer,subject:'11111111-1111-4111-8111-111111111111',kind:'browser' as const,expiresAt:Date.now()/1000+600};
function database(options:{existing?:string;verified?:boolean;missing?:boolean;changed?:boolean}={}) {
  const queries:string[]=[];
  let binding=options.existing;
  let profileReads=0;
  const client={
    async query(sql:string,args?:unknown[]) {
      queries.push(sql);
      if(sql.includes('migration_directory')) throw Error('No legacy directory exists');
      if(sql.includes('FROM neon_auth')) {
        profileReads++;
        return {rows:options.missing?[]:[{email:options.changed&&profileReads>1?'changed@test.invalid':'owner@test.invalid',emailVerified:options.verified??true}]};
      }
      if(sql.startsWith('SELECT identity_id')) return {rows:binding?[{identity_id:binding}]:[]};
      if(sql.startsWith('INSERT INTO provider_identities')) binding=String(args?.[2]);
      return {rows:[]};
    },
    release() {},
  };
  const pool={connect:async()=>client} as unknown as Pool;
  return {service:new NeonAdmission(()=>pool,issuer),queries};
}
it('reuses an existing issuer/subject binding without a legacy directory or account write',async()=>{
  const f=database({existing:'existing-owner',verified:false});
  expect(await f.service.onboard(who)).toEqual({userId:'existing-owner'});
  expect(f.queries.some(sql=>sql.startsWith('INSERT'))).toBe(false);
});
it('enrolls a verified Neon account without reading a legacy directory or matching another owner by email',async()=>{
  const f=database();
  expect((await f.service.onboard(who)).userId).toMatch(/^[a-f0-9-]{36}$/);
  expect(f.queries.filter(sql=>sql.startsWith('INSERT INTO identities'))).toHaveLength(1);
  expect(f.queries.some(sql=>sql.includes('WHERE email'))).toBe(false);
  expect(f.queries.at(-1)).toBe('COMMIT');
});
it('rejects an unverified or missing current managed profile',async()=>{
  for(const options of [{verified:false},{missing:true}]) {
    const f=database(options);
    await expect(f.service.onboard(who)).rejects.toMatchObject({status:409});
    expect(f.queries.at(-1)).toBe('ROLLBACK');
    expect(f.queries.some(sql=>sql.startsWith('INSERT'))).toBe(false);
  }
});
it('rolls back enrollment when the verified mailbox changes during binding',async()=>{
  const f=database({changed:true});
  await expect(f.service.onboard(who)).rejects.toMatchObject({status:409});
  expect(f.queries.at(-1)).toBe('ROLLBACK');
});
it('rejects an expired or different issuer before account writes',async()=>{
  for(const token of [{...who,expiresAt:0},{...who,issuer:'https://other.invalid/db/auth'}]) {
    const f=database();
    await expect(f.service.onboard(token)).rejects.toMatchObject({status:401});
    expect(f.queries.some(sql=>sql.startsWith('INSERT'))).toBe(false);
  }
});
