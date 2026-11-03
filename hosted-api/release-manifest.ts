import { readFileSync,lstatSync,readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { releaseEnvironment } from './release-environment.ts';

interface ReleaseManifest {
  formatVersion:number;environment:string;serverSha:string;studioSha:string;coreSha:string;node:string;
  authority:unknown;files:{path:string;sha256:string}[];
}
/** Deployment reads only the receipt written after complete archive verification. */
function readReceipt(path:string):ReleaseManifest {
  if(process.versions.node!=='24.21.0')throw Error('Release requires Node24.21.0');
  const info=lstatSync(path);
  if(!info.isFile()||info.size>64*1024)throw Error('Invalid release receipt');
  const manifest=JSON.parse(readFileSync(path,'utf8')) as ReleaseManifest;
  if(manifest.formatVersion!==1||!['staging','production'].includes(manifest.environment)||manifest.node!=='24.21.0'||!/^[0-9a-f]{40}$/.test(manifest.serverSha)||
    !/^[0-9a-f]{40}$/.test(manifest.studioSha)||manifest.coreSha!=='97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c')throw Error('Release receipt target mismatch');
  const names=['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'];
  if(!Array.isArray(manifest.files)||manifest.files.length!==4||manifest.files.map(file=>file.path).sort().join(',')!==[...names].sort().join(','))throw Error('Invalid release receipt files');
  return manifest;
}
function verifyFiles(manifest:ReleaseManifest,directory:string,standalone=false):void {
  const names=standalone?['standalone/standalone.js','standalone/package.json']:['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'];
  for(const name of names) {
    const file=resolve(directory,standalone?name.slice('standalone/'.length):name),metadata=lstatSync(file),expected=manifest.files.find(entry=>entry.path===name)!;
    if(!metadata.isFile()||metadata.size>16*1024*1024||!/^[0-9a-f]{64}$/.test(expected.sha256)||createHash('sha256').update(readFileSync(file)).digest('hex')!==expected.sha256)throw Error('Release runtime checksum mismatch');
  }
}
export function reviewedRelease(environment:string|undefined,directory=process.cwd()):ReleaseManifest {
  const manifest=readReceipt(resolve(directory,'dist/release-manifest.json'));
  const server=execFileSync('git',['rev-parse','HEAD'],{cwd:directory,encoding:'utf8'}).trim();
  if(manifest.environment!==environment||manifest.serverSha!==server)throw Error('Release receipt target mismatch');
  const source=resolve(directory,'dist/function'),metadata=lstatSync(source),entries=readdirSync(source,{withFileTypes:true});
  if(!metadata.isDirectory()||metadata.isSymbolicLink()||entries.length!==2||entries.some(entry=>!entry.isFile()||!['index.js','package.json'].includes(entry.name)))throw Error('Release runtime directory mismatch');
  verifyFiles(manifest,resolve(directory,'dist'));return manifest;
}
/** Same API-env renderer for the receipt-bound Function and retained Node/container. */
export function standaloneEnvironment(env:NodeJS.ProcessEnv,directory:string,receipt:string):Record<string,string> {
  const manifest=readReceipt(receipt);
  if(manifest.environment!==env.KENKUI_ENV)throw Error('Release receipt environment mismatch');
  verifyFiles(manifest,directory,true);
  return releaseEnvironment(env,manifest.authority);
}
