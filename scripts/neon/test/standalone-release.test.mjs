import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:net';
import { mkdtemp,mkdir,cp,writeFile,readFile,rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join,resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { spawn,execFileSync } from 'node:child_process';
const pin={apiOrigin:'https://api.staging.kenkui.fm',issuer:'https://auth.invalid/db/auth',jwksUrl:'https://auth.invalid/db/auth/jwks',clientId:'public',resource:'https://api.staging.kenkui.fm'};
const required=['KENKUI_ALLOWED_ORIGINS','KENKUI_ORIGIN_SECRET','R2_ENDPOINT','R2_BUCKET','R2_KEY_SALT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','KENKUI_MODAL_BRIDGE_URL','KENKUI_MODAL_PROXY_KEY','KENKUI_MODAL_PROXY_SECRET','KENKUI_BRIDGE_SECRET','KENKUI_UNSUBSCRIBE_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET','KENKUI_WEB_ORIGIN','KENKUI_STRIPE_API_VERSION'];
async function fixture(extra={},tamper=false) {
  const root=await mkdtemp(join(tmpdir(),'kenkui-guarded-fallback-')),runtime=join(root,'runtime');await mkdir(runtime);
  const manifest={formatVersion:1,environment:'staging',node:'24.21.0',serverSha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),studioSha:'167154028908dbd90a9d42f3da71f7e9b2d46b7b',coreSha:'97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c',authority:pin,files:[]};
  for(const [kind,names] of [['function',['index.js','package.json']],['standalone',['standalone.js','package.json']]])for(const name of names){
    const source=resolve('hosted-api/dist',kind,name),value=await readFile(source);manifest.files.push({path:kind+'/'+name,sha256:createHash('sha256').update(value).digest('hex')});if(kind==='standalone')await cp(source,join(runtime,name));
  }
  const receipt=join(root,'receipt.json');await writeFile(receipt,JSON.stringify(manifest));
  if(tamper)await writeFile(join(runtime,'standalone.js'),Buffer.concat([await readFile(join(runtime,'standalone.js')),Buffer.from('\n// changed after restore\n')]));
  const reservation=createServer();await new Promise(r=>reservation.listen(0,'127.0.0.1',r));const port=reservation.address().port;await new Promise(r=>reservation.close(r));
  const child=spawn(process.execPath,[join(runtime,'standalone.js'),'--receipt',receipt],{env:{PATH:process.env.PATH,...Object.fromEntries(required.map(key=>[key,'synthetic'])),KENKUI_ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ENV:'staging',KENKUI_API_DATABASE_URL:'postgresql://api_runtime@synthetic.invalid/db',KENKUI_ESTIMATED_COST_CENTS_PER_MILLION:'333',PORT:String(port),...extra},stdio:['ignore','pipe','pipe']});
  let stderr='';child.stderr.on('data',chunk=>{stderr+=chunk;});child.stdout.resume();const exit=new Promise(resolve=>child.once('exit',(code,signal)=>resolve({code,signal})));
  return {root,child,port,exit,stderr:()=>stderr,async close(){child.kill('SIGTERM');await exit;await rm(root,{recursive:true,force:true});}};
}
async function within(promise,ms,fallback){let timer;try{return await Promise.race([promise,new Promise(r=>{timer=setTimeout(()=>r(fallback),ms);})]);}finally{clearTimeout(timer);}}
for(const [name,env,error] of [
  ['JWKS-only duplicate authority',{KENKUI_RELEASE_AUTHORITY:JSON.stringify({...pin,jwksUrl:'https://substituted.invalid/jwks'})},'authority override mismatch'],
  ['different release environment',{KENKUI_ENV:'production'},'environment mismatch'],
  ['owner-only copied Render connection',{KENKUI_API_DATABASE_URL:undefined,DATABASE_URL:'postgresql://owner@wrong.invalid/db'},'KENKUI_API_DATABASE_URL'],
])test('actual packaged fallback refuses '+name+' before listening',async()=>{
  const f=await fixture(env);try{const result=await within(f.exit,1200,null);assert.notEqual(result,null,'unguarded fallback kept running');assert.notEqual(result.code,0);assert.match(f.stderr(),new RegExp(error));}
  finally{await f.close();}
});
test('actual packaged fallback rejects runtime bytes changed after restore',async()=>{
  const f=await fixture({},true);try{const result=await within(f.exit,1200,null);assert.notEqual(result,null);assert.notEqual(result.code,0);assert.match(f.stderr(),/Release runtime checksum mismatch/);}finally{await f.close();}
});
test('packaged fallback derives full authority from receipt rather than copied Render auth env',async()=>{
  const f=await fixture({NEON_AUTH_BASE_URL:'https://other.invalid/db/auth',NEON_AUTH_JWKS_URL:'https://other.invalid/jwks',KENKUI_NATIVE_CLIENT_ID:'wrong',KENKUI_AUTH_RESOURCE:'https://other.invalid'});
  try {
    let response;for(let i=0;i<100&&!response;i++){try{response=await fetch('http://127.0.0.1:'+f.port+'/v1/auth/native/config',{headers:{'X-Kenkui-Origin-Secret':'synthetic'}});}catch{await new Promise(r=>setTimeout(r,10));}}
    assert(response,'fallback did not start');assert.equal(response.status,200);const config=await response.json();assert.equal(config.issuer,pin.issuer);assert.equal(config.jwksUrl,pin.jwksUrl);assert.equal(config.clientId,pin.clientId);assert.equal(config.resource,pin.resource);
  }finally{await f.close();}
});
