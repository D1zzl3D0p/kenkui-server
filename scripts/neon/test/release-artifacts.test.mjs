import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp,writeFile,readFile,mkdir,rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync,spawnSync } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { restoreArtifacts,MAX_COMPRESSED_BYTES,MAX_EXPANDED_BYTES } from '../release-artifacts.mjs';

const specs={function:['index.js','package.json'],standalone:['standalone.js','package.json']};
const bytes=name=>Buffer.from('reviewed '+name);
const digest=value=>createHash('sha256').update(value).digest('hex');
async function fixture() {
  const root=await mkdtemp(join(tmpdir(),'kenkui-release-test-')),downloads=join(root,'download');await mkdir(downloads);
  const manifest={files:[]};
  await mkdir(join(root,'hosted-api/dist'),{recursive:true});await writeFile(join(root,'hosted-api/dist/release-manifest.json'),'prior receipt');
  for(const [kind,names] of Object.entries(specs)) {
    await mkdir(join(root,'hosted-api/dist',kind),{recursive:true});
    for(const name of names) {manifest.files.push({path:kind+'/'+name,sha256:digest(bytes(name))});await writeFile(join(root,'hosted-api/dist',kind,name),'prior');}
    archive(join(downloads,kind+'.tar.gz'),names.map(name=>({name,data:bytes(name).toString('base64')})));
  }
  return {root,downloads,manifest};
}
function archive(path,entries) {
  execFileSync('python3',['-c',String.raw`
import base64,io,json,sys,tarfile
with tarfile.open(sys.argv[1],'w:gz',format=tarfile.USTAR_FORMAT) as archive:
    for row in json.loads(sys.argv[2]):
        value=base64.b64decode(row.get('data','')); member=tarfile.TarInfo(row['name']); member.size=len(value)
        if row.get('type'): member.type=row['type'].encode(); member.linkname='../../../outside.txt'
        archive.addfile(member,io.BytesIO(value))
`,path,JSON.stringify(entries)]);
}
async function intact(f) {
  for(const [kind,names] of Object.entries(specs))for(const name of names)assert.equal(await readFile(join(f.root,'hosted-api/dist',kind,name),'utf8'),'prior');
  await assert.rejects(readFile(join(f.root,'outside.txt')));
  assert.equal(await readFile(join(f.root,'hosted-api/dist/release-manifest.json'),'utf8'),'prior receipt');
}
test('valid regular archives install the exact four reviewed files',async()=>{
  const f=await fixture();try {await restoreArtifacts(f.downloads,f.manifest,f.root);for(const [kind,names]of Object.entries(specs))for(const name of names)assert.deepEqual(await readFile(join(f.root,'hosted-api/dist',kind,name)),bytes(name));}
  finally{await rm(f.root,{recursive:true,force:true});}
});
for(const [name,entries] of [
  ['wrong hash',[{name:'index.js',data:Buffer.from('tampered').toString('base64')},{name:'package.json',data:bytes('package.json').toString('base64')}]],
  ['traversal',[{name:'../../../outside.txt',data:bytes('index.js').toString('base64')},{name:'package.json',data:bytes('package.json').toString('base64')}]],
  ['extra member',[...specs.function.map(name=>({name,data:bytes(name).toString('base64')})),{name:'extra',data:'YQ=='}]],
  ['duplicate',[{name:'index.js',data:bytes('index.js').toString('base64')},{name:'index.js',data:bytes('index.js').toString('base64')}]],
  ['symlink',[{name:'index.js',type:'2'},{name:'package.json',data:bytes('package.json').toString('base64')}]],
  ['hardlink',[{name:'index.js',type:'1'},{name:'package.json',data:bytes('package.json').toString('base64')}]],
  ['PAX record',[{name:'metadata',type:'x',data:'YQ=='},...specs.function.map(name=>({name,data:bytes(name).toString('base64')}))]],
])test('optimized-interpreter environment rejects '+name+' before any prior output changes',async()=>{
  const f=await fixture();try {
    archive(join(f.downloads,'function.tar.gz'),entries);
    const script=`import {restoreArtifacts} from ${JSON.stringify(new URL('../release-artifacts.mjs',import.meta.url).href)};await restoreArtifacts(...JSON.parse(process.argv[1]));`;
    const result=spawnSync(process.execPath,['--input-type=module','-e',script,JSON.stringify([f.downloads,f.manifest,f.root])],{env:{...process.env,PYTHONOPTIMIZE:'1'},encoding:'utf8',timeout:5000});
    assert.notEqual(result.status,0);assert.match(result.stderr,/Invalid release archive|Release archive checksum/);await intact(f);
  }finally{await rm(f.root,{recursive:true,force:true});}
});
for(const [name,content] of [
  ['compressed size',()=>Buffer.alloc(MAX_COMPRESSED_BYTES+1)],
  ['expanded gzip bomb',()=>gzipSync(Buffer.alloc(MAX_EXPANDED_BYTES+1))],
])test('bounds '+name+' before header scanning or writes',async()=>{
  const f=await fixture();try {await writeFile(join(f.downloads,'function.tar.gz'),content());await assert.rejects(restoreArtifacts(f.downloads,f.manifest,f.root),/Release archive .*limit/);await intact(f);}
  finally{await rm(f.root,{recursive:true,force:true});}
});
test('invalid second bundle preserves both prior runtimes and the prior receipt',async()=>{
  const f=await fixture();try {
    archive(join(f.downloads,'standalone.tar.gz'),[{name:'standalone.js',data:Buffer.from('tampered').toString('base64')},{name:'package.json',data:bytes('package.json').toString('base64')}]);
    await assert.rejects(restoreArtifacts(f.downloads,f.manifest,f.root),/checksum/);await intact(f);
  }finally{await rm(f.root,{recursive:true,force:true});}
});
