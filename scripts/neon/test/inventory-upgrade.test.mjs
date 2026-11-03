import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join,resolve } from 'node:path';
import { captureInventory } from '../inventory.mjs';
import { verifyRestore } from '../restore-verify.mjs';
const base='postgresql://postgres@127.0.0.1:55438/postgres',python=resolve('.venv/bin/python'),pg='/opt/homebrew/opt/libpq/bin/';
function py(code,data={}) {return JSON.parse(execFileSync(python,['-c',code],{input:JSON.stringify(data),encoding:'utf8',stdio:['pipe','pipe','pipe']}));}
function sql(url,query) {return py("import sys,json,psycopg\nd=json.load(sys.stdin)\nwith psycopg.connect(d['url'],autocommit=True) as c:c.execute(d['query'])\nprint('null')",{url,query});}
function review(source,target,extra={}) {return {format:'restore-review-v1',purpose:'restored-copy',environment:'staging',source:{target:source.target,captureDigest:source.captureDigest},destination:{target:target.target,captureDigest:target.captureDigest},approvalRef:'synthetic-independent-review',attestedBy:'synthetic-operator',attestedAt:new Date().toISOString(),migrationIds:[],newSequences:{},...extra,attestedAt:new Date().toISOString()};}
test('actual pre0004 settlement ->0004 ->pre0008 source copy ->all pending migrations preserves history', {skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async()=>{
  const root=mkdtempSync(join(tmpdir(),'kenkui-actual-upgrade-'));
  const names=py(`import json,uuid,tempfile,shutil,psycopg
from pathlib import Path
from psycopg import sql
from kenkui_server.storage.postgres_database import PostgresDatabase
names=['rehearsal_test_'+uuid.uuid4().hex for _ in range(2)]
with psycopg.connect('${base}',autocommit=True) as c:
 for n in names:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(n)))
url='${base}'.removesuffix('postgres')+names[0];db=PostgresDatabase(url)
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp)
 for f in sorted(Path('migrations/postgresql').glob('*.sql')):
  if f.name<'0004':shutil.copy(f,p/f.name)
 db.migrate(p)
 with psycopg.connect(url) as c:
  c.execute("INSERT INTO identities(id,workos_subject) VALUES ('00000000-0000-0000-0000-000000000001','synthetic-historical')")
  c.execute("INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id) VALUES ('historical-job','{}','succeeded',0,'{}','00000000-0000-0000-0000-000000000001')")
  c.execute("INSERT INTO credit_accounts(id,identity_id,available_credits) VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001',4)")
  c.execute("INSERT INTO credit_authorizations(id,job_id,account_id,credits,status,created_at,finalized_at) VALUES ('00000000-0000-0000-0000-000000000003','historical-job','00000000-0000-0000-0000-000000000002',3,'settled','2020-01-01Z','2020-01-02Z')")
 for f in sorted(Path('migrations/postgresql').glob('*.sql')):
  if f.name<'0008':shutil.copy(f,p/f.name)
 db.migrate(p)
db.close();print(json.dumps(names))`);
  const urls=names.map(n=>base.replace(/postgres$/,n));
  const options=i=>({databaseUrl:urls[i],environment:'staging',mode:i?'restored-copy':'source',synthetic:true,target:{host:'127.0.0.1',port:55438,database:names[i],projectId:'synthetic-project',branchId:'synthetic-'+i},managedAuth:'absent-reviewed-source',r2Keys:[],keySalt:'synthetic'});
  try {
    const source=await captureInventory(options(0)),dump=join(root,'copy.dump');
    assert.equal(source.creditInvariants.historicalSettlementCandidates.length,1);
    execFileSync(pg+'pg_dump',['--format=custom','--no-owner','--no-acl','--file',dump,urls[0]],{stdio:'pipe'});
    execFileSync(pg+'pg_restore',['--no-owner','--no-acl','--exit-on-error','--dbname',urls[1],dump],{stdio:'pipe'});
    const restored=await captureInventory(options(1));
    assert.throws(()=>verifyRestore(source,restored,review(source,restored)),/historical|allocation/i);
    const historicalSettlements={assertion:'independently-reviewed-historical-pre-0004',sourceCaptureDigest:source.captureDigest,records:source.creditInvariants.historicalSettlementCandidates};
    assert.equal(verifyRestore(source,restored,review(source,restored,{historicalSettlements})).status,'verified-copy');
    py("import json,sys\nfrom kenkui_server.storage.postgres_database import PostgresDatabase\nd=json.load(sys.stdin);db=PostgresDatabase(d['url']);db.migrate();db.close();print('null')",{url:urls[1]});
    const migratedOptions={...options(1),mode:'migrated-target',sourceProjection:source},after=await captureInventory(migratedOptions);
    const migrationIds=after.migrationIds.filter(id=>!source.migrationIds.includes(id));
    assert.deepEqual(migrationIds,['0008_migration_recovery.sql','0009_migration_fixture_quarantine.sql','0010_migration_legacy_fixture_finance.sql']);
    const seq='"public"."migration_rate_events_id_seq"';assert(after.sequences[seq]);
    const approved=review(source,after,{purpose:'migrated-target',historicalSettlements,migrationIds,reviewedTargetDigest:after.fullDigest,newSequences:{[seq]:after.sequences[seq]}});
    assert.throws(()=>verifyRestore(source,after,{...approved,newSequences:{}}),/sequence/);
    assert.equal(verifyRestore(source,after,approved).status,'verified-migration');
    for(const [change,undo] of [
      ["UPDATE credit_authorizations SET credits=4","UPDATE credit_authorizations SET credits=3"],
      ["UPDATE credit_authorizations SET finalized_at=NULL","UPDATE credit_authorizations SET finalized_at='2020-01-02Z'"],
      ["UPDATE credit_authorizations SET finalized_at=clock_timestamp()","UPDATE credit_authorizations SET finalized_at='2020-01-02Z'"],
      ["INSERT INTO credit_allocations VALUES ('00000000-0000-0000-0000-000000000003','legacy:00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000002',1)","DELETE FROM credit_allocations"],
    ]) {sql(urls[1],change);try {const bad=await captureInventory(migratedOptions);assert.throws(()=>verifyRestore(source,bad,review(source,bad,{...approved,destination:{target:bad.target,captureDigest:bad.captureDigest},reviewedTargetDigest:bad.fullDigest})));}finally{sql(urls[1],undo);}}
    for(const finalized of ['NULL','clock_timestamp()']) {
      sql(urls[0],'UPDATE credit_authorizations SET finalized_at='+finalized);
      try {
        const ambiguous=await captureInventory(options(0));
        const claimed={assertion:'independently-reviewed-historical-pre-0004',sourceCaptureDigest:ambiguous.captureDigest,records:ambiguous.creditInvariants.historicalSettlementCandidates};
        assert.throws(()=>verifyRestore(ambiguous,after,review(ambiguous,after,{purpose:'migrated-target',migrationIds,newSequences:approved.newSequences,reviewedTargetDigest:after.fullDigest,historicalSettlements:claimed})),/unresolved chronology/);
      } finally {sql(urls[0],"UPDATE credit_authorizations SET finalized_at='2020-01-02Z'");}
    }
    sql(urls[1],"INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id) VALUES ('new-job','{}','succeeded',0,'{}','00000000-0000-0000-0000-000000000001'); INSERT INTO credit_authorizations(id,job_id,account_id,credits,status,created_at,finalized_at) VALUES (gen_random_uuid(),'new-job','00000000-0000-0000-0000-000000000002',1,'settled',clock_timestamp(),clock_timestamp())");
    const bad=await captureInventory(migratedOptions);
    const fabricated={...historicalSettlements,records:bad.creditInvariants.historicalSettlementCandidates};
    assert.throws(()=>verifyRestore(source,bad,review(source,bad,{...approved,destination:{target:bad.target,captureDigest:bad.captureDigest},reviewedTargetDigest:bad.fullDigest,historicalSettlements:fabricated})),/historical|allocation/i);
  } finally {for(const n of names)sql(base,'DROP DATABASE '+n+' WITH (FORCE)');rmSync(root,{recursive:true,force:true});}
});
