import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync,rmSync,readFileSync,statSync,writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join,resolve } from 'node:path';
import { captureInventory,writePrivateManifest } from '../inventory.mjs';
import { assertEmptyTarget,verifyRestore } from '../restore-verify.mjs';

const base='postgresql://postgres@127.0.0.1:55438/postgres',python=resolve('.venv/bin/python');
const pg='/opt/homebrew/opt/libpq/bin/';
function py(code,data={}) {return JSON.parse(execFileSync(python,['-c',code],{input:JSON.stringify(data),encoding:'utf8',stdio:['pipe','pipe','pipe']}));}
function sql(url,statement) {return py("import sys,json,psycopg\nd=json.load(sys.stdin)\nwith psycopg.connect(d['url'],autocommit=True) as c:\n c.execute(d['sql'])\nprint('null')",{url,sql:statement});}
test('real disposable copy, mutations, migration delta, managed/private permissions and safe CLI', {skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async t=>{
  const root=mkdtempSync(join(tmpdir(),'kenkui-restore-verifier-'));
  const names=py(`import json,uuid,psycopg
from psycopg import sql
from kenkui_server.storage.postgres_database import PostgresDatabase
names=['rehearsal_test_'+uuid.uuid4().hex for _ in range(2)]
with psycopg.connect('${base}',autocommit=True) as c:
 for n in names:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(n)))
url='${base}'.removesuffix('postgres')+names[0]
db=PostgresDatabase(url);db.migrate();db.close()
with psycopg.connect(url) as c:
 c.execute("CREATE SCHEMA neon_auth; CREATE TABLE neon_auth.credentials (secret text); INSERT INTO neon_auth.credentials VALUES ('synthetic-secret-not-for-stdout')")
 c.execute("INSERT INTO identities(id,workos_subject) VALUES ('00000000-0000-0000-0000-000000000001','synthetic-only')")
 c.execute("INSERT INTO credit_accounts(id,identity_id,available_credits) VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001',4)")
 c.execute("INSERT INTO credit_lots(id,account_id,kind,reference,credited) VALUES ('legacy:00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002','legacy','legacy:00000000-0000-0000-0000-000000000002',4)")
 c.execute("INSERT INTO credit_ledger_entries(id,account_id,kind,credits,reference) VALUES (gen_random_uuid(),'00000000-0000-0000-0000-000000000002','reservation',-3,'synthetic-historical')")
 c.execute("INSERT INTO payment_events(provider,provider_event_id) VALUES ('stripe','synthetic-event')")
 c.execute("INSERT INTO assets(id,path,sha256,format,owner_id) VALUES ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000003',repeat('a',64),'epub','00000000-0000-0000-0000-000000000001')")
 c.execute("CREATE TABLE private_release_extra (secret text); INSERT INTO private_release_extra VALUES ('synthetic-private-evidence')")
 c.execute("INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id) VALUES ('synthetic-job','{}','succeeded',0,'{}','00000000-0000-0000-0000-000000000001')")
 c.execute("INSERT INTO job_checkpoints(id,job_id,checkpoint_key,sha256,size_bytes,metadata_json,ready) VALUES ('synthetic-cp','synthetic-job','chapter:1',repeat('a',64),12,'{}',true)")
print(json.dumps(names))`);
  const urls=names.map(n=>base.replace(/postgres$/,n)),dump=join(root,'synthetic.dump');
  // Existing R2 derivation, fixed independently checked salt/key vector for this synthetic source.
  const key='sources/72aa2268ef8aeab045bf4256afc3cdcc9027e1824f90520e23ac5340ab65197c';
  const options=i=>({databaseUrl:urls[i],environment:'staging',target:{host:'127.0.0.1',database:names[i],projectId:'synthetic-project',branchId:'synthetic-'+i},r2Keys:[key,'checkpoints/a9bb44f5451bbbc0fe393a9c4ad8b7a8fb4c0c14516542a14c595c96a26cff6c'],keySalt:'synthetic',managedAuth:'required'});
  try {
    await assertEmptyTarget(options(1));
    execFileSync(pg+'pg_dump',['--format=custom','--no-owner','--no-acl','--file',dump,urls[0]],{stdio:'pipe'});
    execFileSync(pg+'pg_restore',['--no-owner','--no-acl','--exit-on-error','--dbname',urls[1],dump],{stdio:'pipe'});
    const source=await captureInventory(options(0)),target=await captureInventory(options(1));
    await t.test('all tables/auth/private evidence and exact established quarantine digest survive empty-target copy',()=>{
      assert.equal(verifyRestore(source,target).status,'verified-copy');
      assert.equal(target.tables['neon_auth.credentials'].count,'1');assert.equal(target.tables['public.private_release_extra'].count,'1');
      assert.equal(target.creditInvariants.accounts[0].openingResidualCredits,'7');
      assert.equal(target.creditInvariants.accounts[0].availableCredits,'4');
      const exact=py("import sys,json,psycopg\nfrom psycopg.rows import dict_row\nfrom kenkui_server.storage.migration_fixture_quarantine import restored_snapshot_digest\nd=json.load(sys.stdin)\nwith psycopg.connect(d['url'],row_factory=dict_row) as c: print(json.dumps(restored_snapshot_digest(c)))",{url:urls[1]});
      assert.equal(target.restoredSnapshotDigest,exact);
    });
    await t.test('occupied target rejection never deletes user/auth rows',async()=>{
      await assert.rejects(()=>assertEmptyTarget(options(1)),/occupied/);assert.equal((await captureInventory(options(1))).fullDigest,target.fullDigest);
    });
    await t.test('wrong environment or endpoint pin fails closed',async()=>{
      assert.throws(()=>verifyRestore(source,{...target,environment:'production'}),/environment/);
      await assert.rejects(()=>captureInventory({...options(1),target:{...options(1).target,host:'wrong.invalid'}}),/target/);
    });
    await t.test('missing row, altered credit, duplicate payment and orphan owner are detected from actual SQL',async()=>{
      for(const [change,undo] of [
        ["DELETE FROM private_release_extra","INSERT INTO private_release_extra VALUES ('synthetic-private-evidence')"],
        ["UPDATE credit_ledger_entries SET credits=-2","UPDATE credit_ledger_entries SET credits=-3"],
        ["ALTER TABLE payment_events DROP CONSTRAINT payment_events_pkey; INSERT INTO payment_events SELECT * FROM payment_events","DELETE FROM payment_events WHERE ctid NOT IN (SELECT min(ctid) FROM payment_events GROUP BY provider,provider_event_id); ALTER TABLE payment_events ADD PRIMARY KEY(provider,provider_event_id)"],
        ["SET session_replication_role=replica; UPDATE assets SET owner_id='00000000-0000-0000-0000-000000000099'; SET session_replication_role=origin","UPDATE assets SET owner_id='00000000-0000-0000-0000-000000000001'"],
      ]) {sql(urls[1],change);try {await assert.rejects(async()=>verifyRestore(source,await captureInventory(options(1))));} finally {sql(urls[1],undo);}}
    });
    await t.test('missing R2 object and orphan tracked reference block verification',async()=>{
      await assert.rejects(async()=>verifyRestore(source,await captureInventory({...options(1),r2Keys:[]})),/R2/);
      sql(urls[1],"INSERT INTO stored_objects(id,object_kind,opaque_object_key,resource_id,terminal_at) VALUES (gen_random_uuid(),'source','orphan-key','missing-source',now())");
      try {await assert.rejects(async()=>verifyRestore(source,await captureInventory({...options(1),r2Keys:[key,'orphan-key']})),/R2/);} finally {sql(urls[1],"DELETE FROM stored_objects");}
    });
    await t.test('retained object tracking may reference an existing source tombstone',async()=>{
      sql(urls[1],`UPDATE assets SET deleted_at=now(); INSERT INTO stored_objects(id,object_kind,opaque_object_key,resource_id,terminal_at) VALUES (gen_random_uuid(),'source','${key}','00000000-0000-0000-0000-000000000003',now())`);
      try {assert.equal((await captureInventory(options(1))).r2.orphanReferences,0);}
      finally {sql(urls[1],"DELETE FROM stored_objects; UPDATE assets SET deleted_at=NULL");}
    });
    await t.test('RLS policy drift cannot pass an exact source copy',async()=>{
      sql(urls[1],"ALTER TABLE private_release_extra ENABLE ROW LEVEL SECURITY; CREATE POLICY synthetic_policy ON private_release_extra USING (true)");
      try {await assert.rejects(async()=>verifyRestore(source,await captureInventory(options(1))),/digest|table/);}
      finally {sql(urls[1],"DROP POLICY synthetic_policy ON private_release_extra; ALTER TABLE private_release_extra DISABLE ROW LEVEL SECURITY");}
    });
    await t.test('known additive DDL requires original column projection, exact migration delta and full target attestation',async()=>{
      sql(urls[1],"ALTER TABLE identities ADD COLUMN synthetic_new text; CREATE TABLE additive_release_private (id text); INSERT INTO schema_migrations VALUES ('0011_synthetic_only.sql')");
      try {
        const after=await captureInventory({...options(1),sourceProjection:source});
        assert.throws(()=>verifyRestore(source,after),/migration|table|digest/);
        assert.throws(()=>verifyRestore(source,after,{mode:'migrated',migrationIds:['0011_synthetic_only.sql']}),/attestation/);
        const review={mode:'migrated',migrationIds:['0011_synthetic_only.sql'],reviewedTargetDigest:after.fullDigest};
        assert.equal(verifyRestore(source,after,review).status,'verified-migration');
        const tampered=structuredClone(after);tampered.tables['public.additive_release_private'].count='2';
        assert.throws(()=>verifyRestore(source,tampered,review),/digest/);
        assert.throws(()=>verifyRestore(source,after,{...review,migrationIds:[]}),/migration/);
        sql(urls[1],"UPDATE credit_ledger_entries SET credits=-2");
        const changed=await captureInventory({...options(1),sourceProjection:source});
        assert.throws(()=>verifyRestore(source,changed,{...review,reviewedTargetDigest:changed.fullDigest}),/preserv/);
        sql(urls[1],"UPDATE credit_ledger_entries SET credits=-3");
      } finally {sql(urls[1],"ALTER TABLE identities DROP COLUMN synthetic_new; DROP TABLE additive_release_private; DELETE FROM schema_migrations WHERE name='0011_synthetic_only.sql'");}
    });
    await t.test('managed Auth addition is an explicit reviewed target transition, never a silent source omission',async()=>{
      sql(urls[0],"DROP SCHEMA neon_auth CASCADE");
      const legacy=await captureInventory({...options(0),managedAuth:'absent-reviewed-source'});
      const withAuth=await captureInventory({...options(1),sourceProjection:legacy});
      const review={mode:'migrated',migrationIds:[],reviewedTargetDigest:withAuth.fullDigest};
      assert.throws(()=>verifyRestore(legacy,withAuth,review),/auth scope/);
      assert.equal(verifyRestore(legacy,withAuth,{...review,managedAuthAdded:true}).status,'verified-migration');
      await assert.rejects(()=>captureInventory(options(0)));
    });
    await t.test('private evidence is mode0600 exclusive and stdout/errors contain no rows or credentials',async()=>{
      const path=join(root,'manifest.json');await writePrivateManifest(path,target);
      assert.equal(statSync(path).mode&0o777,0o600);assert(readFileSync(path,'utf8').includes('fullDigest'));
      await assert.rejects(()=>writePrivateManifest(path,target));
      await assert.rejects(()=>writePrivateManifest(resolve('deploy/neon/never-private.json'),target),/outside Git/);
      const configPath=join(root,'config.json'),cliPath=join(root,'cli-inventory.json');
      const {databaseUrl,...config}=options(1);writeFileSync(configPath,JSON.stringify(config),{mode:0o600});
      const cli=execFileSync(process.execPath,['scripts/neon/inventory.mjs','capture',configPath,cliPath],{encoding:'utf8',env:{PATH:process.env.PATH,KENKUI_INVENTORY_DATABASE_URL:databaseUrl.replace('postgres@','postgres:synthetic-secret-not-for-stdout@')}});
      assert.deepEqual(Object.keys(JSON.parse(cli)),['tables','fullDigest','restoredSnapshotDigest']);
      assert(!cli.includes('synthetic-secret'));assert(!cli.includes('synthetic-private-evidence'));assert(!cli.includes('synthetic-only'));
      const result=await import('node:child_process').then(({spawnSync})=>spawnSync(process.execPath,['scripts/neon/inventory.mjs','invalid'],{encoding:'utf8',env:{...process.env,KENKUI_INVENTORY_DATABASE_URL:'postgresql://synthetic-secret-not-for-stdout@invalid/db'}}));
      assert.notEqual(result.status,0);assert(!result.stdout.includes('synthetic-secret'));assert(!result.stderr.includes('synthetic-secret'));
    });
    await t.test('private managed-auth denial or FORCE RLS cannot silently omit tables',async()=>{
      sql(urls[1],"ALTER TABLE neon_auth.credentials ENABLE ROW LEVEL SECURITY; ALTER TABLE neon_auth.credentials FORCE ROW LEVEL SECURITY");
      // Superuser correctly sees all rows even under FORCE RLS. A distinct unprivileged SQL role does not.
      const role='rehearsal_role_'+names[1].slice(-16);
      sql(urls[1],`CREATE ROLE ${role} LOGIN; GRANT CONNECT ON DATABASE ${names[1]} TO ${role}; GRANT USAGE ON SCHEMA public,neon_auth TO ${role}; GRANT SELECT ON ALL TABLES IN SCHEMA public TO ${role}`);
      try {
        const restricted={...options(1),databaseUrl:urls[1].replace('postgres@',role+'@')};
        await assert.rejects(()=>captureInventory(restricted));
        sql(urls[1],`GRANT SELECT,UPDATE ON ALL TABLES IN SCHEMA public,neon_auth TO ${role}; GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO ${role}`);
        // SELECT privilege is now present; row_security=off must raise rather than filter private rows.
        assert.throws(()=>sql(restricted.databaseUrl,"SET row_security=off; SELECT * FROM neon_auth.credentials"),/row-level security/);
        await assert.rejects(()=>captureInventory(restricted));
      }
      finally {sql(urls[1],`DROP OWNED BY ${role}; DROP ROLE ${role}; ALTER TABLE neon_auth.credentials NO FORCE ROW LEVEL SECURITY; ALTER TABLE neon_auth.credentials DISABLE ROW LEVEL SECURITY`);}
    });
  } finally {
    for(const name of names)sql(base,'DROP DATABASE '+name+' WITH (FORCE)');
    rmSync(root,{recursive:true,force:true});
  }
});
