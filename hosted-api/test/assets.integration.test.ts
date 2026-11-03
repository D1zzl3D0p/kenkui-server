import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, writeFile, access, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { Client, Pool } from 'pg';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AssetStore } from '../src/db/assets.js';
import { objectKey } from '../src/storage/r2.js';

const run=promisify(execFile),url=process.env.KENKUI_TEST_DATABASE_URL;
const owner='11111111-1111-4111-8111-111111111111';
const other='22222222-2222-4222-8222-222222222222';
const sha256='a'.repeat(64);
describe.skipIf(!url)('shared source upload lifecycle on actual PostgreSQL',()=>{
  let admin:Client,pool:Pool,schema:string,directory:string,store:AssetStore;
  async function python(operation:string,id:string,extra='') {
    const result=await run('../.venv/bin/python',['test/helpers/assets_database.py',schema,operation,id,directory,extra],{env:{...process.env,KENKUI_TEST_DATABASE_URL:url!}});
    return JSON.parse(result.stdout) as {deleted:number;id?:string;key?:string};
  }
  beforeEach(async()=>{
    expect(url).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
    schema='identity_test_'+randomUUID().replaceAll('-','');directory=await mkdtemp(join(tmpdir(),'kenkui-assets-'));
    admin=new Client({connectionString:url!,ssl:false});await admin.connect();await admin.query(`CREATE SCHEMA ${schema}`);
    pool=new Pool({connectionString:url!,ssl:false,max:5,options:`-c search_path=${schema}`,application_name:schema});
    await run('../.venv/bin/python',['test/helpers/identity_database.py',schema,'initialize'],{env:{...process.env,KENKUI_TEST_DATABASE_URL:url!}});
    store=new AssetStore(()=>pool);
  });
  afterEach(async()=>{await pool?.end();if(admin){if(schema)await admin.query(`DROP SCHEMA ${schema} CASCADE`);await admin.end();}if(directory)await rm(directory,{recursive:true,force:true});});
  it('matches the actual Python private worker key for an owned render attempt',async()=>{
    const identifier=randomUUID()+'.'+randomUUID();
    expect((await python('key',identifier)).key).toBe(objectKey('salt','artifact',identifier));
  });
  it('keeps incomplete uploads private and finalizes interchangeably through Python and TS without changing owner IDs',async()=>{
    const id=randomUUID(),asset={id,format:'epub',sha256};
    await store.beginUpload(asset,owner);
    await expect(store.requireOwned(id,owner)).rejects.toMatchObject({status:404});
    expect((await pool.query('SELECT count(*) FROM assets WHERE id=$1',[id])).rows[0].count).toBe('0');
    expect((await python('complete',id)).id).toBe(id);
    expect(await store.requireOwned(id,owner)).toEqual(asset);
    await expect(store.requireOwned(id,other)).rejects.toMatchObject({status:403});
    expect(await store.beginUpload(asset,owner)).toEqual(asset);
    expect((await pool.query('SELECT count(*) FROM source_upload_intents')).rows[0].count).toBe('0');
    const second=randomUUID();await python('reserve',second);
    expect(await store.completeUpload(second,owner)).toEqual({id:second,format:'epub',sha256});
  });
  it('cannot publish an untransformed Python cover intent and preserves semantic generation on TS completion',async()=>{
    const semantic=randomUUID();expect((await python('cover_select',semantic)).id).toBe(semantic);
    await expect(store.completeUpload(semantic,owner)).rejects.toMatchObject({status:409});
    await store.beginUpload({id:semantic,format:'epub',sha256},owner);
    await store.completeUpload(semantic,owner);
    expect((await pool.query('SELECT cover_operation_id,cover_generation FROM assets WHERE id=$1',[semantic])).rows)
      .toEqual([{cover_operation_id:semantic,cover_generation:0}]);
    expect((await python('cover_select',semantic)).id).toBe(semantic);
  });
  it('rejects conflicting owner/digest retries and atomically rolls back metadata finalization errors',async()=>{
    const id=randomUUID(),asset={id,format:'epub',sha256};await store.beginUpload(asset,owner);
    await expect(store.beginUpload({...asset,sha256:'b'.repeat(64)},owner)).rejects.toMatchObject({status:409});
    await expect(store.completeUpload(id,other)).rejects.toMatchObject({status:403});
    await pool.query(`ALTER TABLE assets ADD CONSTRAINT synthetic_fail CHECK(id<> '${id}')`);
    await expect(store.completeUpload(id,owner)).rejects.toThrow();
    expect((await pool.query('SELECT source_id FROM source_upload_intents')).rows).toEqual([{source_id:id}]);
    await pool.query('ALTER TABLE assets DROP CONSTRAINT synthetic_fail');
    expect(await store.completeUpload(id,owner)).toEqual(asset);
  });
  it('retains expired tombstones after missing/failed deletes, fences late finalization and reaps a late PUT on the next daily pass',async()=>{
    const id=randomUUID();await store.beginUpload({id,format:'epub',sha256},owner);
    await pool.query(`UPDATE source_upload_intents SET expires_at=clock_timestamp()-interval '1 second',next_cleanup_at=clock_timestamp()-interval '1 second' WHERE source_id=$1`,[id]);
    // Crash before PUT: first cleanup tolerates a missing object.
    expect((await python('retention',id)).deleted).toBe(1);
    expect((await pool.query('SELECT cleanup_started FROM source_upload_intents WHERE source_id=$1',[id])).rows).toEqual([{cleanup_started:true}]);
    await writeFile(join(directory,id),'late provider completion');
    await expect(store.completeUpload(id,owner)).rejects.toMatchObject({status:409});
    await pool.query('UPDATE source_upload_intents SET next_cleanup_at=clock_timestamp() WHERE source_id=$1',[id]);
    expect((await python('reap',id,'fail')).deleted).toBe(0);
    await access(join(directory,id));
    expect((await pool.query('SELECT count(*) FROM source_upload_intents WHERE source_id=$1',[id])).rows[0].count).toBe('1');
    await pool.query('UPDATE source_upload_intents SET next_cleanup_at=clock_timestamp() WHERE source_id=$1',[id]);
    expect((await python('reap',id)).deleted).toBe(1);
    await expect(access(join(directory,id))).rejects.toThrow();
    await expect(python('complete',id)).rejects.toThrow();
  });
  it('reaps late PUTs using completed asset tombstones and preserves active/recent jobs',async()=>{
    const id=randomUUID();await store.beginUpload({id,format:'epub',sha256},owner);await store.completeUpload(id,owner);
    await pool.query('UPDATE assets SET deleted_at=clock_timestamp() WHERE id=$1',[id]);
    await pool.query(`UPDATE jobs SET spec_json=jsonb_set(spec_json::jsonb,'{source_id}',to_jsonb($1::text))::text WHERE id='book-before'`,[id]);
    await writeFile(join(directory,id),'late completed upload');
    expect((await python('reap',id)).deleted).toBe(0);await access(join(directory,id));
    await pool.query(`UPDATE jobs SET status='succeeded',terminal_at=clock_timestamp()-interval '25 hours' WHERE id='book-before'`);
    await pool.query('UPDATE assets SET next_source_cleanup_at=clock_timestamp() WHERE id=$1',[id]);
    expect((await python('reap',id)).deleted).toBe(1);await expect(access(join(directory,id))).rejects.toThrow();
    await writeFile(join(directory,id),'second late completion');
    await pool.query('UPDATE assets SET next_source_cleanup_at=clock_timestamp() WHERE id=$1',[id]);
    expect((await python('reap',id)).deleted).toBe(1);await expect(access(join(directory,id))).rejects.toThrow();
    expect((await pool.query('SELECT id FROM assets WHERE id=$1',[id])).rows).toEqual([{id}]);
  });
  it('checks expiry after waiting for the shared finalization lock and cleanup skips competing transactions',async()=>{
    const id=randomUUID();await store.beginUpload({id,format:'epub',sha256},owner);
    await pool.query(`UPDATE source_upload_intents SET expires_at=clock_timestamp()+interval '1 second',next_cleanup_at=clock_timestamp()-interval '1 second' WHERE source_id=$1`,[id]);
    const blocker=await pool.connect();await blocker.query('BEGIN');
    await blocker.query('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',[id]);
    const pending=store.completeUpload(id,owner).then(value=>({value}),error=>({error}));
    try{
      await expect.poll(async()=>Number((await admin.query(`SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND wait_event='advisory'`,[schema])).rows[0].count)).toBe(1);
      await expect.poll(async()=>(await pool.query('SELECT clock_timestamp()>=expires_at AS expired FROM source_upload_intents WHERE source_id=$1',[id])).rows[0].expired).toBe(true);
      expect((await python('reap',id)).deleted).toBe(0);
    }finally{await blocker.query('ROLLBACK');blocker.release();}
    expect(await pending).toMatchObject({error:{status:409}});
    expect((await python('reap',id)).deleted).toBe(1);
  });
  it('serializes overlapping finalizations and never re-creates tracking for a completed deterministic asset',async()=>{
    const id=randomUUID(),asset={id,format:'epub',sha256};await store.beginUpload(asset,owner);
    const blocker=await pool.connect();await blocker.query('BEGIN');
    await blocker.query('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',[id]);
    const pending=Promise.all([store.completeUpload(id,owner),store.completeUpload(id,owner)]);
    try{
      await expect.poll(async()=>Number((await admin.query(`SELECT count(*) FROM pg_stat_activity WHERE application_name=$1 AND wait_event='advisory'`,[schema])).rows[0].count)).toBe(2);
      // Sweeper skips a busy writer without holding any provider transaction.
      expect((await python('reap',id)).deleted).toBe(0);
    }finally{await blocker.query('ROLLBACK');blocker.release();}
    expect(await pending).toEqual([asset,asset]);
    await writeFile(join(directory,id),'completed source');
    expect(await store.beginUpload(asset,owner)).toEqual(asset);
    expect((await python('reap',id)).deleted).toBe(0);await access(join(directory,id));
    expect((await pool.query('SELECT count(*) FROM source_upload_intents')).rows[0].count).toBe('0');
  });
});
