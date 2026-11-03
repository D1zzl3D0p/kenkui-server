import assert from 'node:assert/strict';
import { test } from 'node:test';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { captureInventory,checkedOptions,inventoryDigest,captureDigest } from '../inventory.mjs';
import { assertEmptyTarget,verifyRestore } from '../restore-verify.mjs';
const base='postgresql://postgres@127.0.0.1:55438/postgres',python=resolve('.venv/bin/python');
const identity=pin=>({projectId:pin.projectId,branchId:pin.branchId,database:pin.database});
function review(source,target) {return {format:'restore-review-v1',purpose:'restored-copy',environment:'staging',source:{target:source.target,captureDigest:source.captureDigest,databaseIdentity:identity(source.target)},destination:{target:target.target,captureDigest:target.captureDigest,databaseIdentity:identity(target.target)},approvalRef:'synthetic-independent-review',attestedBy:'synthetic-operator',attestedAt:new Date().toISOString()};}
function fabricated(pin,mode) {
  const m={format:'kenkui-restore-inventory-v2',environment:'staging',target:pin,mode,capturedAt:new Date().toISOString(),managedAuth:'absent-reviewed-source',sourceProjectionCaptureDigest:null,
    types:{},databaseCollation:{},tables:{'"public"."synthetic"':{count:'0',sha256:'a'.repeat(64),columns:['id'],definitionDigest:'b'.repeat(64)}},schemaObjects:{},sequences:{},migrationIds:[],sourceProjection:{},restoredSnapshotDigest:null,
    creditInvariants:{accounts:[],orphanReferences:0,duplicatePayments:0,invalidLots:0,crossOwnerAuthorizations:0,invalidAllocations:0,invalidLotAllocations:0,historicalSettlementCandidates:[]},r2:{references:[],missingObjects:0,orphanReferences:0}};
  m.fullDigest=inventoryDigest(m);m.captureDigest=captureDigest(m);return m;
}
test('reviewed provider identity rejects trailing-dot and different-host aliases but permits different databases',()=>{
  const pin={host:'db.invalid',port:5432,database:'kenkui',projectId:'synthetic-project',branchId:'synthetic-source'},source=fabricated(pin,'source');
  for(const host of ['db.invalid.','other.invalid']) {
    const target=fabricated({...pin,host},'restored-copy');
    for(const selected of [source,target])assert.doesNotThrow(()=>checkedOptions({databaseUrl:`postgresql://synthetic@${selected.target.host}:5432/kenkui?sslmode=verify-full`,environment:'staging',mode:selected.mode,target:selected.target,managedAuth:'absent-reviewed-source',r2Keys:[],keySalt:'synthetic'}));
    const old=review(source,target);delete old.source.databaseIdentity;delete old.destination.databaseIdentity;
    assert.throws(()=>verifyRestore(source,target,old));
    assert.throws(()=>verifyRestore(source,target,review(source,target)));
  }
  const independent=fabricated({...pin,database:'independent_copy'},'restored-copy');
  assert.equal(verifyRestore(source,independent,review(source,independent)).status,'verified-copy');
});
function py(code,data={}) {return JSON.parse(execFileSync(python,['-c',code],{input:JSON.stringify(data),encoding:'utf8',stdio:['pipe','pipe','pipe']}));}
function sql(url,query) {return py("import sys,json,psycopg\nd=json.load(sys.stdin)\nwith psycopg.connect(d['url'],autocommit=True) as c:\n q=c.execute(d['query']); rows=q.fetchall() if q.description else None\nprint(json.dumps(rows))",{url,query});}
const names=()=>Array.from({length:2},()=> 'rehearsal_test_'+randomUUID().replaceAll('-',''));
const url=name=>base.replace(/postgres$/,name);
const config=(name,mode,branch='synthetic-source')=>({databaseUrl:url(name),environment:'staging',mode,synthetic:true,target:{host:'127.0.0.1',port:55438,database:name,projectId:'synthetic-project',branchId:branch},managedAuth:'absent-reviewed-source',r2Keys:[],keySalt:'synthetic'});
function migrate(address) {py("import json,sys\nfrom kenkui_server.storage.postgres_database import PostgresDatabase\nd=json.load(sys.stdin);db=PostgresDatabase(d['url']);db.migrate();db.close();print('null')",{url:address});}
test('enum-only destination is occupied and enum-label drift changes exact-copy content',{skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async()=>{
  const dbs=names();
  try {
    for(const n of dbs)sql(base,'CREATE DATABASE '+n);
    sql(url(dbs[1]),"CREATE TYPE public.pending_restore_type AS ENUM ('occupied')");
    await assert.rejects(()=>assertEmptyTarget(config(dbs[1],'restored-copy')));
    sql(url(dbs[1]),'DROP TYPE public.pending_restore_type');
    for(const n of dbs){migrate(url(n));sql(url(n),"CREATE TYPE public.mood AS ENUM ('first','last'); CREATE TABLE public.enum_values(value public.mood)");}
    const source=await captureInventory(config(dbs[0],'source')),same=await captureInventory(config(dbs[1],'restored-copy'));
    assert.equal(verifyRestore(source,same,review(source,same)).status,'verified-copy');
    sql(url(dbs[1]),"ALTER TYPE public.mood ADD VALUE 'middle' BEFORE 'last'");
    const changed=await captureInventory(config(dbs[1],'restored-copy'));
    assert.throws(()=>verifyRestore(source,changed,review(source,changed)),/digest|type|schema/);
    sql(url(dbs[1]),'CREATE DOMAIN public.unsupported_domain AS text');
    await assert.rejects(()=>captureInventory(config(dbs[1],'restored-copy')),/schema/);
  } finally {for(const n of dbs)sql(base,'DROP DATABASE IF EXISTS '+n+' WITH (FORCE)');}
});
test('ICU catalog reader supports known locale fields and refuses missing effective rules',()=>{
  assert.deepEqual(py(`import json,runpy
read=runpy.run_path('scripts/neon/inventory.py')['database_collation']
class Catalog:
 def __init__(self,value):self.value=value
 def execute(self,query):return self
 def fetchone(self):return {'catalog':self.value}
base={'datcollate':'C.UTF-8','datctype':'C.UTF-8','datcollversion':'synthetic','datlocprovider':'i'}
for locale in ('datlocale','daticulocale'):
 result=read(Catalog(base|{locale:'en','daticurules':'& z < a'}))
 assert result['daticurules']=='& z < a' and result[locale]=='en'
try:read(Catalog(base|{'datlocale':'en'}))
except ValueError:pass
else:raise AssertionError('unrepresented ICU rules accepted')
print('true')`),true);
});
test('ICU databases with identical locale and different effective rules cannot pass exact-copy verification',{skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async()=>{
  const dbs=names();
  try {
    sql(base,`CREATE DATABASE ${dbs[0]} TEMPLATE template0 LOCALE_PROVIDER icu ICU_LOCALE 'en'`);
    sql(base,`CREATE DATABASE ${dbs[1]} TEMPLATE template0 LOCALE_PROVIDER icu ICU_LOCALE 'en' ICU_RULES '& z < a'`);
    for(const n of dbs)migrate(url(n));
    assert.deepEqual(sql(url(dbs[0]),"SELECT 'a'::text < 'z'::text"),[[true]]);
    assert.deepEqual(sql(url(dbs[1]),"SELECT 'a'::text < 'z'::text"),[[false]]);
    const source=await captureInventory(config(dbs[0],'source')),target=await captureInventory(config(dbs[1],'restored-copy'));
    assert.notDeepEqual(source.databaseCollation,target.databaseCollation);
    assert.throws(()=>verifyRestore(source,target,review(source,target)),/digest|schema/);
  } finally {for(const n of dbs)sql(base,'DROP DATABASE IF EXISTS '+n+' WITH (FORCE)');}
});
