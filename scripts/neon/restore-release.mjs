import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [environment,artifactPath,studioPath]=process.argv.slice(2);
if(!['staging','production'].includes(environment)||!artifactPath||!studioPath||process.versions.node!=='24.21.0')throw Error('Use Node24.21.0 and select environment, downloaded release directory, Studio checkout');
const manifest=JSON.parse(await readFile(resolve(artifactPath,'manifest.json'),'utf8'));
const git=(...args)=>execFileSync('git',args,{encoding:'utf8'}).trim();
if(manifest.formatVersion!==1||manifest.environment!==environment||manifest.node!=='24.21.0'||manifest.serverSha!==git('rev-parse','HEAD')||manifest.studioSha!==git('-C',studioPath,'rev-parse','HEAD')||manifest.coreSha!=='97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c')throw Error('Release target/revision mismatch');
for(const checkout of ['.',studioPath])execFileSync('git',['-C',checkout,'diff','--quiet','HEAD','--']);
const {loadRelease}=await import(pathToFileURL(resolve(studioPath,'scripts/release-config.mjs')).href);
const authority=JSON.parse(loadRelease(environment).KENKUI_AUTH_AUTHORITIES)[0];
if(JSON.stringify(authority)!==JSON.stringify(manifest.authority))throw Error('Release authority mismatch');
const expected=['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'];
if(!Array.isArray(manifest.files)||manifest.files.length!==4||manifest.files.map(x=>x.path).sort().join(',')!==expected.sort().join(',')||manifest.files.some(x=>!/^[0-9a-f]{64}$/.test(x.sha256)))throw Error('Invalid release file manifest');
// No tar extraction of arbitrary paths, symlinks, devices or private files. Only
// the four regular bounded members are read, verified and installed locally.
execFileSync('python3',['-c',String.raw`
import hashlib,json,pathlib,shutil,sys,tarfile
root=pathlib.Path(sys.argv[1]); expected={x['path']:x['sha256'] for x in json.loads(sys.argv[2])}; contents={}
for kind in ('function','standalone'):
    with tarfile.open(root/(kind+'.tar.gz'),'r:gz') as archive:
        members=archive.getmembers()
        assert len(members)==2
        for member in members:
            path=kind+'/'+member.name
            assert path in expected and path not in contents and member.isfile() and 0<member.size<16*1024*1024
            value=archive.extractfile(member).read()
            assert hashlib.sha256(value).hexdigest()==expected[path]
            contents[path]=value
assert set(contents)==set(expected)
for kind in ('function','standalone'):
    target=pathlib.Path('hosted-api/dist')/kind
    if target.exists(): shutil.rmtree(target)
    target.mkdir(parents=True)
for name,value in contents.items(): (pathlib.Path('hosted-api/dist')/name).write_bytes(value)
`,resolve(artifactPath),JSON.stringify(manifest.files)],{stdio:'inherit'});
console.log('Verified and installed exact reviewed runtime artifacts locally; no rebuild, secret access or provider action.');
