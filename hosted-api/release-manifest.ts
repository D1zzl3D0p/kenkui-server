import { readFileSync,lstatSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

interface ReleaseManifest {
  formatVersion:number;environment:string;serverSha:string;studioSha:string;coreSha:string;node:string;
  authority:unknown;files:{path:string;sha256:string}[];
}
/** Deployment reads only the receipt written after complete archive verification. */
export function reviewedRelease(environment:string|undefined,directory=process.cwd()):ReleaseManifest {
  if(process.versions.node!=='24.21.0')throw Error('Release requires Node24.21.0');
  const path=resolve(directory,'dist/release-manifest.json'),info=lstatSync(path);
  if(!info.isFile()||info.size>64*1024)throw Error('Invalid release receipt');
  const manifest=JSON.parse(readFileSync(path,'utf8')) as ReleaseManifest;
  const server=execFileSync('git',['rev-parse','HEAD'],{cwd:directory,encoding:'utf8'}).trim();
  if(manifest.formatVersion!==1||manifest.environment!==environment||manifest.node!=='24.21.0'||manifest.serverSha!==server||
    !/^[0-9a-f]{40}$/.test(manifest.studioSha)||manifest.coreSha!=='97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c')throw Error('Release receipt target mismatch');
  const names=['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'];
  if(!Array.isArray(manifest.files)||manifest.files.length!==4||manifest.files.map(file=>file.path).sort().join(',')!==[...names].sort().join(','))throw Error('Invalid release receipt files');
  for(const name of names) {
    const file=resolve(directory,'dist',name),metadata=lstatSync(file),expected=manifest.files.find(entry=>entry.path===name)!;
    if(!metadata.isFile()||metadata.size>16*1024*1024||!/^[0-9a-f]{64}$/.test(expected.sha256)||createHash('sha256').update(readFileSync(file)).digest('hex')!==expected.sha256)throw Error('Release runtime checksum mismatch');
  }
  return manifest;
}
