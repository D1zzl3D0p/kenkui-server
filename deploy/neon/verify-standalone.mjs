import { lstatSync,readFileSync,readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';

const [directory,receipt]=process.argv.slice(2);
if(!directory||!receipt||process.versions.node!=='24.21.0')throw Error('Use Node24.21.0, restored standalone directory and verified receipt');
const info=lstatSync(receipt);if(!info.isFile()||info.size>64*1024)throw Error('Invalid fallback receipt');
const manifest=JSON.parse(readFileSync(receipt,'utf8'));
if(manifest.formatVersion!==1||manifest.node!=='24.21.0'||!['staging','production'].includes(manifest.environment)||!/^[0-9a-f]{40}$/.test(manifest.serverSha)||manifest.coreSha!=='97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c')throw Error('Invalid fallback release');
const names=['package.json','standalone.js'];
if(readdirSync(directory).sort().join(',')!==names.join(',')||!Array.isArray(manifest.files)||manifest.files.length!==4)throw Error('Unexpected fallback files');
for(const name of names) {
  const path=resolve(directory,name),metadata=lstatSync(path),matches=manifest.files.filter(file=>file.path==='standalone/'+name);
  if(matches.length!==1||!metadata.isFile()||metadata.size>16*1024*1024||!/^[0-9a-f]{64}$/.test(matches[0].sha256)||createHash('sha256').update(readFileSync(path)).digest('hex')!==matches[0].sha256)throw Error('Fallback runtime checksum mismatch');
}
console.log(JSON.stringify({event:'fallback.verified',serverSha:manifest.serverSha}));
