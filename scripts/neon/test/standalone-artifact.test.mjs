import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp,writeFile,mkdir,rm } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

test('fallback build/start verifier accepts only the exact reviewed standalone bytes',async()=>{
  const root=await mkdtemp(join(tmpdir(),'kenkui-fallback-bytes-')),directory=join(root,'runtime'),receipt=join(root,'release-manifest.json');
  try {
    await mkdir(directory);const files=[];
    for(const name of ['index.js','package.json','standalone.js']) {
      const value=Buffer.from('reviewed '+name),sha256=createHash('sha256').update(value).digest('hex');
      if(name!=='index.js')await writeFile(join(directory,name),value);
      files.push({path:(name==='index.js'?'function/':'standalone/')+name,sha256});
    }
    files.push({path:'function/package.json',sha256:files[1].sha256});
    await writeFile(receipt,JSON.stringify({formatVersion:1,node:'24.21.0',environment:'staging',serverSha:'a'.repeat(40),coreSha:'97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c',files}));
    const run=()=>spawnSync(process.execPath,[fileURLToPath(new URL('../../../deploy/neon/verify-standalone.mjs',import.meta.url)),directory,receipt],{encoding:'utf8'});
    assert.equal(run().status,0);
    await writeFile(join(directory,'standalone.js'),'unreviewed rebuild');const tampered=run();assert.notEqual(tampered.status,0);assert.match(tampered.stderr,/checksum mismatch/);
  }finally{await rm(root,{recursive:true,force:true});}
});
