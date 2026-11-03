import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { proxyRequest } from '../../../ingress/src/index.ts';
const {Hono}=createRequire(new URL('../../../hosted-api/package.json',import.meta.url))('hono');

for(const [method,path,alias,binding] of [
  ['POST','/v1/jobs','/v1/%6Aobs','COSTLY_LIMIT'],
  ['POST','/v1/jobs/preflight','/%76%31/jobs/%70reflight','COSTLY_LIMIT'],
  ['GET','/v1/auth/login','/v1/%61uth/%6cogin','LOGIN_LIMIT'],
])test('edge agrees with installed Hono routing for '+method+' '+alias,async()=>{
  const app=new Hono();app.on(method,path,context=>context.text('matched'));
  const keys={PUBLIC_LIMIT:[],LOGIN_LIMIT:[],COSTLY_LIMIT:[]};
  const env={ORIGIN_URL:'https://native.invalid',ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'synthetic',...Object.fromEntries(Object.entries(keys).map(([name,values])=>[name,{async limit({key}){values.push(key);return {success:true};}}]))};
  for(const wirePath of [path,alias]) {
    const request=new Request('https://api.invalid'+wirePath,{method,headers:{'CF-Connecting-IP':'192.0.2.1'}});
    assert.equal(await (await app.fetch(request.clone())).text(),'matched');
    const result=await proxyRequest(request,env,async upstream=>{assert.equal(new URL(upstream.url).pathname,wirePath);return app.fetch(upstream);});assert.equal(await result.text(),'matched');
  }
  assert.deepEqual(keys[binding],[binding==='LOGIN_LIMIT'?'login:192.0.2.1':'costly:192.0.2.1',binding==='LOGIN_LIMIT'?'login:192.0.2.1':'costly:192.0.2.1']);assert.deepEqual(keys.PUBLIC_LIMIT,[]);
});
