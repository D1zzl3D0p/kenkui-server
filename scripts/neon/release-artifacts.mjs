import { createReadStream } from 'node:fs';
import { lstat,mkdir,rm,writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { createGunzip } from 'node:zlib';
import { Transform,Writable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { resolve } from 'node:path';

export const MAX_COMPRESSED_BYTES=8*1024*1024;
export const MAX_EXPANDED_BYTES=16*1024*1024;
const files={function:['index.js','package.json'],standalone:['standalone.js','package.json']};
const invalid=()=>Error('Invalid release archive');
const zeros=bytes=>bytes.every(byte=>byte===0);
function field(bytes) {
  const end=bytes.indexOf(0);
  if(end<0||!zeros(bytes.subarray(end)))throw invalid();
  const value=bytes.subarray(0,end);
  if(!value.every(byte=>byte>=32&&byte<=126))throw invalid();
  return value.toString('ascii');
}
function octal(bytes) {
  const value=bytes.toString('ascii').replace(/[\0 ]+$/,'');
  if(!/^[0-7]+$/.test(value))throw invalid();
  const number=Number.parseInt(value,8);if(!Number.isSafeInteger(number))throw invalid();return number;
}
async function boundedGunzip(path) {
  const info=await lstat(path);
  if(!info.isFile()||info.size>MAX_COMPRESSED_BYTES)throw Error('Release archive compressed limit');
  let compressed=0,expanded=0;const chunks=[];
  await pipeline(createReadStream(path),new Transform({transform(chunk,_encoding,done){
    compressed+=chunk.length;done(compressed>MAX_COMPRESSED_BYTES?Error('Release archive compressed limit'):null,chunk);
  }}),createGunzip(),new Writable({write(chunk,_encoding,done){
    expanded+=chunk.length;
    if(expanded>MAX_EXPANDED_BYTES){done(Error('Release archive expanded limit'));return;}
    chunks.push(chunk);done();
  }}));
  return Buffer.concat(chunks,expanded);
}
/** Parse only our two regular USTAR headers. No PAX/GNU extensions or metadata list. */
function members(bytes,names) {
  const contents=new Map();let offset=0;
  for(let count=0;count<2;count++) {
    const header=bytes.subarray(offset,offset+512);if(header.length!==512)throw invalid();
    const name=field(header.subarray(0,100)),size=octal(header.subarray(124,136));
    const checksum=header.reduce((sum,byte,index)=>sum+(index>=148&&index<156?32:byte),0);
    if(!names.includes(name)||contents.has(name)||header[156]!==48||!zeros(header.subarray(157,257))||
      header.subarray(257,263).toString('ascii')!=='ustar\0'||header.subarray(263,265).toString('ascii')!=='00'||
      !zeros(header.subarray(345,500))||octal(header.subarray(148,156))!==checksum||size<1||size>MAX_EXPANDED_BYTES)throw invalid();
    const start=offset+512,end=start+size,padded=start+Math.ceil(size/512)*512;
    if(padded>bytes.length||!zeros(bytes.subarray(end,padded)))throw invalid();
    contents.set(name,bytes.subarray(start,end));offset=padded;
  }
  if(contents.size!==2||bytes.length%512!==0||bytes.length-offset<1024||!zeros(bytes.subarray(offset)))throw invalid();
  return contents;
}
export async function readReleaseArtifacts(artifactPath,manifest) {
  if(process.versions.node!=='24.21.0')throw Error('Release requires Node24.21.0');
  const expected=Object.entries(files).flatMap(([kind,names])=>names.map(name=>kind+'/'+name));
  if(!Array.isArray(manifest.files)||manifest.files.length!==4||manifest.files.map(x=>x.path).sort().join(',')!==[...expected].sort().join(',')||manifest.files.some(x=>!/^[0-9a-f]{64}$/.test(x.sha256)))throw invalid();
  const hashes=new Map(manifest.files.map(item=>[item.path,item.sha256])),contents=new Map();
  // Both archives and all hashes are checked before the caller receives installable bytes.
  for(const [kind,names] of Object.entries(files)) {
    const parsed=members(await boundedGunzip(resolve(artifactPath,kind+'.tar.gz')),names);
    for(const name of names) {
      const key=kind+'/'+name,value=parsed.get(name);
      if(!value||createHash('sha256').update(value).digest('hex')!==hashes.get(key))throw Error('Release archive checksum mismatch');
      contents.set(key,value);
    }
  }
  return contents;
}
export async function restoreArtifacts(artifactPath,manifest,root=process.cwd()) {
  const contents=await readReleaseArtifacts(artifactPath,manifest);
  // Every destination is derived from this local constant, never an archive name.
  for(const [kind,names] of Object.entries(files)) {
    const directory=resolve(root,'hosted-api/dist',kind);
    await rm(directory,{recursive:true,force:true});await mkdir(directory,{recursive:true});
    for(const name of names)await writeFile(resolve(directory,name),contents.get(kind+'/'+name));
  }
  await writeFile(resolve(root,'hosted-api/dist/release-manifest.json'),JSON.stringify(manifest,null,2)+'\n');
}
