import assert from 'node:assert/strict';
import { mkdtemp, cp, rm, readdir, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { buildRuntime } from './build.mjs';

const directory=await mkdtemp(join(tmpdir(),'kenkui-packaged-'));
try {
  assert.deepEqual((await readdir('dist/function')).sort(),['index.js','package.json']);
  assert.deepEqual((await readdir('dist/standalone')).sort(),['package.json','standalone.js']);
  await cp('dist',directory,{recursive:true});
  const source=await readFile(join(directory,'function/index.js'),'utf8');assert(!source.includes('AUTH_BROWSER_SOURCE'));
  // No ancestor node_modules: these exact release files must import independently.
  process.env.KENKUI_ENV='staging';process.env.KENKUI_ORIGIN_SECRET='synthetic';
  const {default:handler}=await import(pathToFileURL(join(directory,'function/index.js')).href);
  assert.equal((await handler.fetch(new Request('https://api.invalid/v1/health',{headers:{'x-kenkui-origin-secret':'synthetic'}}))).status,200);
  assert.equal((await handler.fetch(new Request('https://api.invalid/v1/health'))).status,403);
  const {serveApi}=await import(pathToFileURL(join(directory,'standalone/standalone.js')).href);
  const server=serveApi(undefined,{hostname:'127.0.0.1',port:0});
  await new Promise(resolve=>server.once('listening',resolve));
  try {const response=await fetch(`http://127.0.0.1:${server.address().port}/v1/health`,{headers:{'x-kenkui-origin-secret':'synthetic'}});assert.equal(response.status,200);await response.text();}
  finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
  await buildRuntime('test/helpers/packaged-contract.ts',join(directory,'contract.mjs'));
  await new Promise((resolve,reject)=>{const child=spawn(process.execPath,[join(directory,'contract.mjs')],{stdio:'inherit',env:process.env,cwd:directory});child.once('error',reject);child.once('exit',code=>code===0?resolve():reject(Error('Packaged contract failed: '+code)));});
  console.log('clean archive smoke passed (no runtime node_modules)');
} finally {await rm(directory,{recursive:true,force:true});}
