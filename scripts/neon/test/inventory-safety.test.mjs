import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync,mkdirSync,writeFileSync,existsSync,rmSync,chmodSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { checkedOptions,writePrivateManifest } from '../inventory.mjs';

const options={databaseUrl:'postgresql://synthetic@db.invalid:5432/kenkui?sslmode=verify-full',environment:'staging',mode:'source',
  target:{host:'db.invalid',port:5432,database:'kenkui',projectId:'synthetic-project',branchId:'synthetic-source'},managedAuth:'absent-reviewed-source',r2Keys:[],keySalt:'synthetic'};
test('offline libpq validation rejects every TLS/endpoint override before connecting',()=>{
  assert.doesNotThrow(()=>checkedOptions(options));
  for(const suffix of ['&sslmode=disable','&sslmode=verify-full','&host=other.invalid','&hostaddr=127.0.0.1','&dbname=other','&port=55438','&service=other','&options=-csearch_path=public'])
    assert.throws(()=>checkedOptions({...options,databaseUrl:options.databaseUrl+suffix}),/target|connection|TLS/);
  for(const url of ['postgresql://synthetic@db.invalid,other.invalid:5432/kenkui?sslmode=verify-full','postgresql://synthetic@db.invalid:5432,55438/kenkui?sslmode=verify-full','postgresql://synthetic@127.0.0.1:55438/kenkui?host=other.invalid'])
    assert.throws(()=>checkedOptions({...options,databaseUrl:url}));
  assert.throws(()=>checkedOptions({...options,target:{...options.target,port:55438}}));
  assert.throws(()=>checkedOptions({...options,synthetic:true,databaseUrl:'postgresql://synthetic@127.0.0.1:55438/kenkui',target:{...options.target,host:'127.0.0.1',port:55438}}));
});
test('private placement refuses unavailable/uncertain Git classification and Git metadata',async()=>{
  const root=mkdtempSync(join(tmpdir(),'kenkui-private-placement-')),original=process.env.PATH;
  try {
    process.env.PATH='';await assert.rejects(()=>writePrivateManifest(join(root,'missing-git.json'),{}));assert(!existsSync(join(root,'missing-git.json')));
    const fake=join(root,'fake');mkdirSync(fake);writeFileSync(join(fake,'git'),'#!/bin/sh\necho "fatal: detected dubious ownership" >&2\nexit 128\n',{mode:0o700});
    process.env.PATH=fake;await assert.rejects(()=>writePrivateManifest(join(root,'uncertain.json'),{}));assert(!existsSync(join(root,'uncertain.json')));
    process.env.PATH=original;
    const repo=join(root,'repo');mkdirSync(repo);execFileSync('git',['init','--quiet',repo]);chmodSync(join(repo,'.git','objects'),0o700);
    await assert.rejects(()=>writePrivateManifest(join(repo,'.git','objects','private.json'),{}));assert(!existsSync(join(repo,'.git','objects','private.json')));
    const loose=join(root,'loose');mkdirSync(loose);chmodSync(loose,0o777);await assert.rejects(()=>writePrivateManifest(join(loose,'private.json'),{}));
  } finally {process.env.PATH=original;rmSync(root,{recursive:true,force:true});}
});
