import { execFileSync } from 'node:child_process';
import { readFile,lstat } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { restoreArtifacts } from './release-artifacts.mjs';

const [environment,artifactPath,studioPath]=process.argv.slice(2);
if(!['staging','production'].includes(environment)||!artifactPath||!studioPath||process.versions.node!=='24.21.0')throw Error('Use Node24.21.0 and select environment, downloaded release directory, Studio checkout');
const manifestPath=resolve(artifactPath,'manifest.json'),manifestInfo=await lstat(manifestPath);
if(!manifestInfo.isFile()||manifestInfo.size>64*1024)throw Error('Invalid release manifest');
const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
const git=(...args)=>execFileSync('git',args,{encoding:'utf8'}).trim();
if(manifest.formatVersion!==1||manifest.environment!==environment||manifest.node!=='24.21.0'||manifest.serverSha!==git('rev-parse','HEAD')||manifest.studioSha!==git('-C',studioPath,'rev-parse','HEAD')||manifest.coreSha!=='97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c')throw Error('Release target/revision mismatch');
for(const checkout of ['.',studioPath])execFileSync('git',['-C',checkout,'diff','--quiet','HEAD','--']);
const {loadRelease}=await import(pathToFileURL(resolve(studioPath,'scripts/release-config.mjs')).href);
const authority=JSON.parse(loadRelease(environment).KENKUI_AUTH_AUTHORITIES)[0];
if(JSON.stringify(authority)!==JSON.stringify(manifest.authority))throw Error('Release authority mismatch');
await restoreArtifacts(artifactPath,manifest);
console.log('Verified and installed exact reviewed runtime artifacts locally; no rebuild, secret access or provider action.');
