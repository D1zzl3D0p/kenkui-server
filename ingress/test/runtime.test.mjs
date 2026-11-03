import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('actual Worker runtime uses native limits, streams bytes and refuses origin redirects',async()=>{
  const script=await readFile('dist/index.js','utf8');
  let upstreamCalls=0;
  const mf=new Miniflare(convertV4MiniflareOptions({workers:[{name:'ingress',modules:true,script,compatibilityDate:'2026-11-03',bindings:{ORIGIN_URL:'https://native.invalid',ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'synthetic'},
    ratelimits:{PUBLIC_LIMIT:{namespace_id:'1',simple:{limit:100,period:60}},LOGIN_LIMIT:{namespace_id:'2',simple:{limit:1,period:60}},COSTLY_LIMIT:{namespace_id:'3',simple:{limit:100,period:60}}},
    outboundService:async request=>{
      upstreamCalls++;assert.equal(request.headers.get('x-kenkui-origin-secret'),'synthetic');
      // The outbound bridge serializes Request metadata. Count/host prove the Worker
      // did not follow the Location; redirect mode itself is covered before fetch.
      assert.equal(new URL(request.url).hostname,'native.invalid');
      if(new URL(request.url).pathname==='/v1/auth/login')return new Response(null,{status:303,headers:{location:'https://external.invalid'}});
      return new Response(request.body,{headers:{'content-type':'application/octet-stream'}});
    }}]}));
  try {
    const init={redirect:'manual',headers:{'CF-Connecting-IP':'192.0.2.44','x-kenkui-origin-secret':'caller',authorization:'Bearer synthetic'}};
    const first=await mf.dispatchFetch('https://api.invalid/v1/auth/login',init);assert.equal(first.status,303,await first.text());assert.equal(upstreamCalls,1);
    const limited=await mf.dispatchFetch('https://api.invalid/v1/auth/login',{...init,headers:{...init.headers,authorization:'Bearer changed'}});assert.equal(limited.status,429);assert.equal(upstreamCalls,1);await limited.text();
    const response=await mf.dispatchFetch('https://api.invalid/v1/billing/webhooks/stripe',{...init,method:'POST',body:new Uint8Array([0,255,13,10])});assert.deepEqual(new Uint8Array(await response.arrayBuffer()),new Uint8Array([0,255,13,10]));
  } finally {await mf.dispose();}
});
