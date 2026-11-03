import assert from 'node:assert/strict';
import { test } from 'node:test';
import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync,readFileSync,writeFileSync,rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
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
test('dependency-free definitions refuse unsupported catalogs and positively capture default ACLs', {skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async t=>{
  const [empty,full]=names(),dbs=[empty,full],pg=process.env.KENKUI_PG_BIN;
  assert(pg?.startsWith('/'),'Set KENKUI_PG_BIN to the vetted PostgreSQL 17+ clients');
  const cases=[
    ['pg_cast','CREATE CAST (integer AS date) WITHOUT FUNCTION','DROP CAST (integer AS date)',/CREATE CAST \(integer AS date\) WITHOUT FUNCTION/i],
    ['pg_am','CREATE ACCESS METHOD synthetic_heap TYPE TABLE HANDLER heap_tableam_handler','DROP ACCESS METHOD synthetic_heap',/CREATE ACCESS METHOD synthetic_heap/i],
    ['pg_transform','CREATE TRANSFORM FOR integer LANGUAGE sql (TO SQL WITH FUNCTION int4recv(internal))','DROP TRANSFORM FOR integer LANGUAGE sql',/CREATE TRANSFORM FOR integer LANGUAGE sql/i],
    ['pg_default_acl','ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC','ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC',/ALTER DEFAULT PRIVILEGES .*GRANT SELECT ON TABLES\s+TO PUBLIC/i],
  ];
  const created=[];
  try {
    for(const name of dbs){sql(base,'CREATE DATABASE '+name);created.push(name);}
    migrate(url(full));
    await assertEmptyTarget(config(empty,'restored-copy'));
    const baseline=await captureInventory(config(full,'restored-copy'));
    for(const [catalog,create,drop,dumped] of cases)await t.test(catalog,async t=>{
      for(const name of dbs)sql(url(name),create);
      try {
        const counts=sql(url(empty),`SELECT
          (SELECT count(*) FROM pg_depend WHERE classid='${catalog}'::regclass AND objid=obj.oid)::int,
          (SELECT count(*) FROM pg_shdepend WHERE classid='${catalog}'::regclass AND objid=obj.oid)::int
          FROM pg_catalog.${catalog} obj WHERE obj.oid>=16384`);
        assert.deepEqual(counts,[[0,0]]);
        const dump=execFileSync(resolve(pg,'pg_dump'),['--schema-only',url(empty)],{encoding:'utf8'});
        assert.match(dump,dumped);
        await t.test('empty target',()=>assert.rejects(()=>assertEmptyTarget(config(empty,'restored-copy')),/occupied|unreadable/));
        await t.test('full inventory',async()=>{
          if(catalog!=='pg_default_acl')return assert.rejects(()=>captureInventory(config(full,'restored-copy')),/schema/);
          const captured=await captureInventory(config(full,'restored-copy'));
          assert.notEqual(captured.fullDigest,baseline.fullDigest);
          const policies=Object.values(captured.schemaObjects).filter(o=>o.kind==='default-acl');
          assert.equal(policies.length,1);
          assert.equal(policies[0].owner,'postgres');assert.equal(policies[0].schema,null);
          assert(policies[0].privileges.some(p=>p.grantor==='postgres'&&p.grantee.kind==='public'&&p.privilege==='SELECT'&&!p.grantOption));
        });
      } finally {for(const name of dbs)sql(url(name),drop);}
    });
    await assertEmptyTarget(config(empty,'restored-copy'));
    assert.equal((await captureInventory(config(full,'restored-copy'))).fullDigest,baseline.fullDigest);
  } finally {for(const name of created)sql(base,'DROP DATABASE '+name+' WITH (FORCE)');}
});
test('large objects, standalone collations and schemas cannot be certified empty or omitted from a full capture',{skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async()=>{
  const [name]=names(),address=url(name),options=config(name,'restored-copy');
  sql(base,'CREATE DATABASE '+name);
  try {
    for(const [create,drop] of [["SELECT lo_from_bytea(18765,'abc')","SELECT lo_unlink(18765)"],["CREATE SCHEMA vacant","DROP SCHEMA vacant"],["CREATE FOREIGN DATA WRAPPER unused","DROP FOREIGN DATA WRAPPER unused"],['CREATE COLLATION synthetic FROM "C"','DROP COLLATION synthetic']]){
      sql(address,create);await assert.rejects(()=>assertEmptyTarget(options));sql(address,drop);
    }
    const role='lo_probe_'+name.slice(-16);
    sql(address,'CREATE ROLE '+role+' LOGIN; REVOKE SELECT ON pg_catalog.pg_largeobject_metadata FROM PUBLIC');
    try {await assert.rejects(()=>assertEmptyTarget({...options,databaseUrl:address.replace('postgres@',role+'@')}));}
    finally {sql(address,'GRANT SELECT ON pg_catalog.pg_largeobject_metadata TO PUBLIC; DROP ROLE '+role);}
    migrate(address);
    const without=await captureInventory(options);assert(without.fullDigest);
    sql(address,"SELECT lo_from_bytea(18765,'original')");
    await assert.rejects(()=>captureInventory(options));
    sql(address,"SELECT lo_put(18765,0,'changed')");
    await assert.rejects(()=>captureInventory(options));
    sql(address,'SELECT lo_unlink(18765)');
    assert.equal((await captureInventory(options)).fullDigest,without.fullDigest);
  } finally {sql(base,'DROP DATABASE '+name+' WITH (FORCE)');}
});
test('default ACL decoding is portable, ordered, complete and fails closed on malformed catalogs',()=>{
  assert.equal(py(`import copy,json,runpy,sys
sys.path.insert(0,'scripts/neon')
read=runpy.run_path('scripts/neon/inventory.py')['default_acls']
class Catalog:
 def __init__(self,rows,count=1):self.rows=rows;self.count=count
 def execute(self,query):return self
 def fetchone(self):return {'count':self.count}
 def fetchall(self):return self.rows
first={'oid':20000,'owner_oid':20001,'schema_oid':20002,'object_type':'r','acl_null':False,'acl_items':2,'owner':'Owner,Ω','schema':'a.b','grantor_oid':20003,'grantor':'Other,"grantor','grantee_oid':0,'grantee':None,'privilege':'SELECT','grant_option':False}
second=first|{'grantee_oid':20004,'grantee':'PUBLIC','privilege':'INSERT','grant_option':True}
rows=[first,second]
expected={'@pg_default_acl:["Owner,Ω","a.b","r"]':{'kind':'default-acl','owner':'Owner,Ω','schema':'a.b','objectType':'r','privileges':[
 {'grantor':'Other,"grantor','grantee':{'kind':'public'},'privilege':'SELECT','grantOption':False},
 {'grantor':'Other,"grantor','grantee':{'kind':'role','name':'PUBLIC'},'privilege':'INSERT','grantOption':True}]}}
assert read(Catalog(rows))==expected
reordered=copy.deepcopy(rows[::-1])
for row in reordered:
 for field in ('oid','owner_oid','schema_oid','grantor_oid','grantee_oid'):
  if row[field]:row[field]+=1000
assert read(Catalog(reordered))==expected
empty=first|{'schema_oid':0,'schema':None,'acl_items':0,**{k:None for k in ('grantor_oid','grantor','grantee_oid','grantee','privilege','grant_option')}}
assert read(Catalog([empty]))=={'@pg_default_acl:["Owner,Ω",null,"r"]':{'kind':'default-acl','owner':'Owner,Ω','schema':None,'objectType':'r','privileges':[]}}
def blocked(label,rows,count=1):
 try:read(Catalog(rows,count))
 except ValueError:pass
 else:raise AssertionError('malformed default ACL accepted: '+label)
blocked('lost expanded ACL item',[first])
for change in ({'object_type':'x'},{'object_type':'n'},{'acl_null':True},{'owner':None},{'schema':None},{'grantor':None},{'grantee_oid':99},{'privilege':'EXECUTE'},{'grant_option':1},{'grant_option':True},{'owner_oid':0},{'acl_items':-1}):
 blocked(str(change),[first|change,second])
blocked('lost row',rows,2)
blocked('duplicate tuple',rows+[first])
blocked('duplicate identity with different OID',rows+[first|{'oid':30000},second|{'oid':30000}],2)
blocked('nonempty ACL with empty sentinel',[empty|{'acl_items':1}])
blocked('ambiguous owner for same OID',[first,second|{'owner':'wrong'}])
blocked('empty duplicate sentinel',[empty,empty])
print('true')`),true);
});
test('default ACL native policy restores exact future-object definitions with inert actors', {skip:process.env.KENKUI_TEST_DATABASE_URL!==base},async t=>{
  const dbs=names(),pg=process.env.KENKUI_PG_BIN,root=mkdtempSync(resolve(tmpdir(),'kenkui-native-acl-'));
  assert(pg?.startsWith('/'));
  const tag=randomUUID().replaceAll('-','').slice(0,12);
  const actors=[`acl_${tag} owner,"Ω`,`acl_${tag} reader,.Ω`,`acl_${tag}.other`];
  const quote=s=>'"'+s.replaceAll('"','""')+'"', [owner,reader,other]=actors.map(quote);
  const createActors=()=>{for(const role of actors){sql(base,`CREATE ROLE ${quote(role)} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`);if(!rolesCreated.includes(role))rolesCreated.push(role);}};
  const policies=[
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} REVOKE ALL ON TABLES FROM ${owner}`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} REVOKE ALL ON TYPES FROM PUBLIC,${owner}`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} GRANT USAGE ON SCHEMAS TO ${reader} WITH GRANT OPTION`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public GRANT INSERT ON TABLES TO ${reader} WITH GRANT OPTION`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public GRANT UPDATE,MAINTAIN ON TABLES TO ${other}`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA "a.b" GRANT SELECT ON SEQUENCES TO ${reader} WITH GRANT OPTION`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA "a.b" GRANT USAGE ON SEQUENCES TO PUBLIC`,
    `ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA "a.b" GRANT EXECUTE ON FUNCTIONS TO ${other}`,
  ];
  const data=resolve(root,'data.dump'),native=resolve(root,'native-policy.dump'),list=resolve(root,'default-acl.list'),projected=resolve(root,'default-acl.sql');
  const created=[],rolesCreated=[];
  try {
    assert.equal(Math.floor(Number(sql(base,'SHOW server_version_num')[0][0])/10000),17);
    for(const n of dbs){sql(base,'CREATE DATABASE '+n);created.push(n);}
    createActors();
    const roleOids=sql(base,`SELECT oid::text FROM pg_roles WHERE rolname IN (${actors.map(s=>"'"+s.replaceAll("'","''")+"'").join(',')}) ORDER BY rolname COLLATE "C"`);
    for(const [statement,undo] of [[policies[0],`ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} GRANT ALL ON TABLES TO ${owner}`],['ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC','ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC']]){
      sql(url(dbs[1]),statement);
      await assert.rejects(()=>assertEmptyTarget(config(dbs[1],'restored-copy')),/occupied|unreadable/);
      sql(url(dbs[1]),undo);
    }
    await assertEmptyTarget(config(dbs[1],'restored-copy'));
    migrate(url(dbs[0]));
    sql(url(dbs[0]),'CREATE SCHEMA "a.b"; CREATE TABLE "a.b".unrelated(id int); INSERT INTO "a.b".unrelated VALUES (9)');
    for(const statement of policies)sql(url(dbs[0]),statement);
    const source=await captureInventory(config(dbs[0],'source'));
    const sourcePolicyOids=sql(url(dbs[0]),'SELECT oid::text FROM pg_default_acl');
    const aclEntries=Object.entries(source.schemaObjects).filter(([,o])=>o.kind==='default-acl');
    assert.equal(aclEntries.length,7);
    assert.deepEqual([...new Set(aclEntries.map(([,o])=>o.objectType))].sort(),['S','T','f','n','r']);
    const entry=(schema,kind)=>aclEntries.find(([,o])=>o.owner===actors[0]&&o.schema===schema&&o.objectType===kind)[1];
    assert.deepEqual(entry(null,'r').privileges,[]);
    assert.deepEqual(entry(null,'T').privileges,[]);
    assert.deepEqual(entry(null,'f').privileges,[{grantor:actors[0],grantee:{kind:'role',name:actors[0]},privilege:'EXECUTE',grantOption:false}]);
    assert(entry('public','r').privileges.some(p=>p.grantee.kind==='public'&&p.privilege==='SELECT'));
    assert(entry('public','r').privileges.some(p=>p.grantee.name===actors[1]&&p.grantOption));
    assert(entry('public','r').privileges.some(p=>p.privilege==='MAINTAIN'));
    for(const [key,o] of aclEntries)assert.equal(key,'@pg_default_acl:'+JSON.stringify([o.owner,o.schema,o.objectType]));
    await t.test('source stability includes default grants',async()=>{
      const stable=await captureInventory(config(dbs[0],'source'));
      const stability=target=>({...review(source,target),purpose:'source-stability'});
      assert.equal(verifyRestore(source,stable,stability(stable)).status,'verified-source-stability');
      sql(url(dbs[0]),`ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public GRANT DELETE ON TABLES TO ${reader}`);
      const changed=await captureInventory(config(dbs[0],'source'));
      assert.throws(()=>verifyRestore(source,changed,stability(changed)),/schema|digest/);
      sql(url(dbs[0]),`ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public REVOKE DELETE ON TABLES FROM ${reader}`);
    });
    // Native pg_dump is the independent policy source; no SQL is generated from the manifest.
    execFileSync(resolve(pg,'pg_dump'),['--format=custom','--no-owner','--no-acl','--file',data,url(dbs[0])],{stdio:'pipe'});
    execFileSync(resolve(pg,'pg_dump'),['--format=custom','--schema-only','--file',native,url(dbs[0])],{stdio:'pipe'});
    const toc=execFileSync(resolve(pg,'pg_restore'),['--list',native],{encoding:'utf8'});
    const selected=toc.split('\n').filter(line=>!line.startsWith(';')&&line.includes(' DEFAULT ACL '));
    assert.equal(selected.length,7);writeFileSync(list,selected.reverse().join('\n')+'\n',{mode:0o600});
    execFileSync(resolve(pg,'pg_restore'),['--use-list',list,'--file',projected,native],{stdio:'pipe'});
    const nativeSql=readFileSync(projected,'utf8');
    assert.match(nativeSql,/ALTER DEFAULT PRIVILEGES/);assert.match(nativeSql,/REVOKE ALL ON TABLES/);
    assert.match(nativeSql,/REVOKE ALL ON TYPES/);assert.match(nativeSql,/WITH GRANT OPTION/);
    assert(nativeSql.includes(owner));assert(nativeSql.includes(reader));
    const sourceEpochEnd=await captureInventory(config(dbs[0],'source'));
    assert.equal(verifyRestore(source,sourceEpochEnd,{...review(source,sourceEpochEnd),purpose:'source-stability'}).status,'verified-source-stability');
    // Remove the source and recreate exact-name actors: recovery works from retained artifacts,
    // and actor/catalog OIDs cannot accidentally provide portable identity.
    sql(base,'DROP DATABASE '+dbs[0]+' WITH (FORCE)');created.splice(created.indexOf(dbs[0]),1);
    for(const actor of actors)sql(base,'DROP ROLE '+quote(actor));
    createActors();
    assert.notDeepEqual(sql(base,`SELECT oid::text FROM pg_roles WHERE rolname IN (${actors.map(s=>"'"+s.replaceAll("'","''")+"'").join(',')}) ORDER BY rolname COLLATE "C"`),roleOids);
    assert.deepEqual(sql(base,`SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls FROM pg_roles WHERE rolname IN (${actors.map(s=>"'"+s.replaceAll("'","''")+"'").join(',')})`),actors.map(()=>[false,false,false,false,false,false,false]));
    assert.deepEqual(sql(base,`SELECT count(*)::int FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member OR r.oid=m.roleid WHERE r.rolname IN (${actors.map(s=>"'"+s.replaceAll("'","''")+"'").join(',')})`),[[0]]);
    execFileSync(resolve(pg,'pg_restore'),['--no-owner','--no-acl','--exit-on-error','--dbname',url(dbs[1]),data],{stdio:'pipe'});
    const missing=await captureInventory(config(dbs[1],'restored-copy'));
    assert.deepEqual(missing.tables,source.tables);
    assert.throws(()=>verifyRestore(source,missing,review(source,missing)),/schema|digest/);
    const restoreEntries=entries=>{
      writeFileSync(list,entries.join('\n')+'\n',{mode:0o600});
      execFileSync(resolve(pg,'pg_restore'),['--no-owner','--exit-on-error','--use-list',list,'--dbname',url(dbs[1]),native],{stdio:'pipe'});
    };
    restoreEntries(selected.slice(0,-1));
    const oneMissing=await captureInventory(config(dbs[1],'restored-copy'));
    assert.equal(Object.values(oneMissing.schemaObjects).filter(o=>o.kind==='default-acl').length,6);
    assert.deepEqual(oneMissing.tables,source.tables);
    assert.throws(()=>verifyRestore(source,oneMissing,review(source,oneMissing)),/schema|digest/);
    restoreEntries(selected.slice(-1));
    const target=await captureInventory(config(dbs[1],'restored-copy'));
    assert.equal(verifyRestore(source,target,review(source,target)).status,'verified-copy');
    for(const [oid] of sql(url(dbs[1]),'SELECT oid::text FROM pg_default_acl'))assert(!sourcePolicyOids.some(([old])=>old===oid));
    await t.test('each policy field and an omitted object fail with recomputed hashes and unchanged data',()=>{
      const first=aclEntries.find(([,o])=>o.schema==='public'&&o.objectType==='r')[0];
      const move=(m,change)=>{
        const o=m.schemaObjects[first];delete m.schemaObjects[first];Object.assign(o,change);
        m.schemaObjects['@pg_default_acl:'+JSON.stringify([o.owner,o.schema,o.objectType])]=o;
      };
      const mutations=[
        m=>delete m.schemaObjects[aclEntries[0][0]],
        m=>move(m,{owner:actors[2]}),
        m=>move(m,{schema:'a.b'}),
        m=>move(m,{objectType:'S',privileges:m.schemaObjects[first].privileges.filter(p=>['SELECT','UPDATE'].includes(p.privilege))}),
        m=>{m.schemaObjects[first].privileges[0].grantor=actors[2];},
        m=>{m.schemaObjects[first].privileges[0].grantee={kind:'role',name:actors[2]};},
        m=>{m.schemaObjects[first].privileges[0].privilege='DELETE';},
        m=>{m.schemaObjects[first].privileges[0].grantOption=!m.schemaObjects[first].privileges[0].grantOption;},
      ];
      for(const mutate of mutations){const changed=structuredClone(target);mutate(changed);changed.fullDigest=inventoryDigest(changed);changed.captureDigest=captureDigest(changed);assert.deepEqual(changed.tables,source.tables);assert.throws(()=>verifyRestore(source,changed,review(source,changed)),/schema|digest/);}
    });
    await t.test('actual policy drift fails independent copy',async()=>{
      sql(url(dbs[1]),`ALTER DEFAULT PRIVILEGES FOR ROLE ${owner} IN SCHEMA public REVOKE GRANT OPTION FOR INSERT ON TABLES FROM ${reader}`);
      const changed=await captureInventory(config(dbs[1],'restored-copy'));
      assert.deepEqual(changed.tables,source.tables);
      assert.throws(()=>verifyRestore(source,changed,review(source,changed)),/schema|digest/);
    });
  } finally {
    for(const n of created)sql(base,'DROP DATABASE '+n+' WITH (FORCE)');
    for(const role of rolesCreated)sql(base,'DROP ROLE IF EXISTS '+quote(role));
    rmSync(root,{recursive:true,force:true});
  }
});
test('ICU catalog reader supports known locale fields and refuses missing effective rules',()=>{
  assert.deepEqual(py(`import json,runpy
import sys;sys.path.insert(0,'scripts/neon')
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
