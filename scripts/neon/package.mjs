import { execFileSync } from 'node:child_process';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [environment,studioPath,studioRef]=process.argv.slice(2);
if(!['staging','production'].includes(environment)||!studioPath||!/^[a-f0-9]{40}$/.test(studioRef??''))throw Error('Expected environment, Studio checkout and immutable Studio SHA');
const git=(...args)=>execFileSync('git',args,{encoding:'utf8'}).trim();
for(const checkout of ['.',studioPath]) {
  execFileSync('git',['-C',checkout,'diff','--quiet','HEAD','--'],{stdio:'inherit'});
  if(git('-C',checkout,'ls-files','--others','--exclude-standard'))throw Error('Untracked files in release checkout');
}
if(git('-C',studioPath,'rev-parse','HEAD')!==studioRef)throw Error('Studio checkout does not match release SHA');
const {loadRelease}=await import(pathToFileURL(resolve(studioPath,'scripts/release-config.mjs')).href);
const authority=JSON.parse(loadRelease(environment).KENKUI_AUTH_AUTHORITIES)[0];
const baseline=JSON.parse(await readFile('contracts/neon/baseline.json','utf8'));
if(createHash('sha256').update(await readFile('openapi/v1.json')).digest('hex')!==baseline.artifacts['openapi/v1.json'])throw Error('Hosted frozen schema drift');
const files=[];
for(const [directory,names] of [['function',['index.js','package.json']],['standalone',['standalone.js','package.json']]]) {
  const location='hosted-api/dist/'+directory;
  if(JSON.stringify((await readdir(location)).sort())!==JSON.stringify([...names].sort()))throw Error('Unexpected release artifact file');
  for(const name of names)files.push({path:directory+'/'+name,sha256:createHash('sha256').update(await readFile(location+'/'+name)).digest('hex')});
}
await mkdir('release',{recursive:true});
await writeFile('release/manifest.json',JSON.stringify({formatVersion:1,environment,serverSha:git('rev-parse','HEAD'),studioSha:studioRef,coreSha:'97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c',node:'24.21.0',authority,files},null,2)+'\n');
for(const [directory,names] of [['function',['index.js','package.json']],['standalone',['standalone.js','package.json']]])execFileSync('tar',['-czf','release/'+directory+'.tar.gz','-C','hosted-api/dist/'+directory,...names],{stdio:'inherit'});
console.log('Packaged allowlisted Function and same-auth standalone artifacts; provider deployment is a separate gated action.');
