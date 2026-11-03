import { randomUUID, createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { Client, Pool } from 'pg';
import { expect, it, vi } from 'vitest';
import { releaseEnvironment } from '../release-environment.js';
import { standaloneEnvironment } from '../release-manifest.js';
import { BillingStore } from '../src/db/billing.js';
import { creditsForCharacters } from '../src/billing/job-spec.js';
import { loadConfig } from '../src/config.js';

const url=process.env.KENKUI_TEST_DATABASE_URL;
it.skipIf(!url)('Function and receipt-bound fallback preserve selected nondefault grants, pricing and catalog in real SQL',async()=>{
  expect(url).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
  const schema='billing_test_'+randomUUID().replaceAll('-',''),admin=new Client({connectionString:url,ssl:false});
  await admin.connect();await admin.query('CREATE SCHEMA '+schema);
  const root=mkdtempSync(join(tmpdir(),'kenkui-release-grants-'));
  const pool=new Pool({connectionString:url,ssl:false,options:'-c search_path='+schema});
  try {
    execFileSync('../.venv/bin/python',['test/helpers/billing_database.py',schema,'init']);
    const authority={apiOrigin:'https://api.staging.kenkui.fm',issuer:'https://auth.invalid/db/auth',jwksUrl:'https://auth.invalid/db/auth/jwks',clientId:'public',resource:'https://api.staging.kenkui.fm'};
    const names=['KENKUI_ALLOWED_ORIGINS','KENKUI_ORIGIN_SECRET','R2_ENDPOINT','R2_BUCKET','R2_KEY_SALT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','KENKUI_MODAL_BRIDGE_URL','KENKUI_MODAL_PROXY_KEY','KENKUI_MODAL_PROXY_SECRET','KENKUI_BRIDGE_SECRET','KENKUI_UNSUBSCRIBE_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET','KENKUI_WEB_ORIGIN','KENKUI_STRIPE_API_VERSION'];
    const env={...Object.fromEntries(names.map(name=>[name,'synthetic'])),KENKUI_ENV:'staging',KENKUI_API_DATABASE_URL:url,
      KENKUI_ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_BETA_CREDITS:'25',KENKUI_ESTIMATED_COST_CENTS_PER_MILLION:'333',
      KENKUI_VOICE_SET:'vctk',KENKUI_VOICE_IDS:'eponine',KENKUI_MAX_JOBS:'2',KENKUI_MAX_SPEECH_CHARACTERS:'10000000',KENKUI_MODEL_ALLOWLIST:'openrouter/deepseek/deepseek-v4-flash'};
    const files=['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'].map(path=>{
      const bytes=Buffer.from('synthetic runtime bytes');
      if(path.startsWith('standalone/')){mkdirSync(join(root,'standalone'),{recursive:true});writeFileSync(join(root,path),bytes);}
      return {path,sha256:createHash('sha256').update(bytes).digest('hex')};
    });
    const receipt=join(root,'receipt.json');
    writeFileSync(receipt,JSON.stringify({formatVersion:1,environment:'staging',serverSha:'a'.repeat(40),studioSha:'b'.repeat(40),coreSha:'97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c',node:'24.21.0',authority,files}));
    for(const selected of [releaseEnvironment(env,authority),standaloneEnvironment(env,join(root,'standalone'),receipt)]) {
      // A dropped deploy-time key must fail before a default can mask the SQL grant.
      expect(selected.KENKUI_BETA_CREDITS).toBe('25');
      vi.stubEnv('KENKUI_BETA_CREDITS',selected.KENKUI_BETA_CREDITS);
      vi.stubEnv('KENKUI_ESTIMATED_COST_CENTS_PER_MILLION',selected.KENKUI_ESTIMATED_COST_CENTS_PER_MILLION);
      const owner=randomUUID();await pool.query('INSERT INTO identities(id) VALUES ($1)',[owner]);
      const billing=new BillingStore(()=>pool),account=await billing.accountForOwner(owner);
      expect(account.availableCredits).toBe('25');expect(await billing.accountForOwner(owner)).toEqual(account);
      expect((await pool.query('SELECT credited FROM credit_lots WHERE account_id=$1',[account.id])).rows).toEqual([{credited:'25'}]);
      expect(creditsForCharacters(1_000_000,false)).toBe(666n);expect(creditsForCharacters(1_000_000,true)).toBe(999n);
      const config=loadConfig(selected);expect(config.catalog.voices.length).toBeGreaterThan(1);
      expect(config.catalog.capabilities.casting.models).toEqual(['openrouter/deepseek/deepseek-v4-flash']);
      expect(selected.KENKUI_MAX_SPEECH_CHARACTERS).toBe('10000000');
      expect(selected).not.toHaveProperty('KENKUI_VOICE_IDS');expect(selected).not.toHaveProperty('KENKUI_MAX_JOBS');
    }
  } finally {vi.unstubAllEnvs();await pool.end();await admin.query('DROP SCHEMA '+schema+' CASCADE');await admin.end();rmSync(root,{recursive:true,force:true});}
});
