import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { Client, Pool } from 'pg';
import { expect } from 'vitest';

export const issuer='https://migration.invalid/db/auth';
export const owner='11111111-1111-4111-8111-111111111111';
export const legacy='urn:kenkui:workos:environment_01M0Y347Z9N6FYNTGVY3725X1Y:client_01M0Y37EQMGD270AW5W7NZC1E0';
export const url='postgresql://postgres@127.0.0.1:55438/postgres';
export async function fixture() {
  expect(process.env.KENKUI_TEST_DATABASE_URL).toBe(url);
  const admin=new Client({connectionString:url}); await admin.connect();
  const name='migration_test_'+randomUUID().replaceAll('-','');
  await admin.query('CREATE DATABASE '+name);
  const databaseUrl=url.replace(/postgres$/,name);
  const pool=new Pool({connectionString:databaseUrl});
  const python=async (...args:string[])=>promisify(execFile)('../.venv/bin/python',['test/helpers/migration_fixture.py',name,...args]);
  const close=async()=>{await pool.end();await admin.query('DROP DATABASE '+name);await admin.end();};
  try {
    await promisify(execFile)('../.venv/bin/python',['test/helpers/identity_database.py','identity_test_old','initialize',name]);
    await pool.query('CREATE SCHEMA neon_auth');
    await pool.query('CREATE TABLE neon_auth."user"(id UUID PRIMARY KEY,email TEXT,"emailVerified" BOOLEAN,banned BOOLEAN)');
    await pool.query("UPDATE identities SET workos_subject='user_old',email='alias@old.test',notify_by_email=false WHERE id=$1",[owner]);
    await pool.query("INSERT INTO provider_identities(issuer,subject,identity_id) VALUES ($1,'user_old',$2)",[legacy,owner]);
    await python('activate');
    const profile=async (email='current@new.test',verified=true,banned:boolean|null=null)=>{
      const subject=randomUUID();
      await pool.query('INSERT INTO neon_auth."user" VALUES ($1,$2,$3,$4)',[subject,email,verified,banned]);
      return {issuer,subject,kind:'browser' as const,expiresAt:Math.floor(Date.now()/1000)+600};
    };
    return {pool,profile,python,close};
  } catch(error){await close();throw error;}
}
