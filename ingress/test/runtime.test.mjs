import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { request as httpRequest } from 'node:http';

test('actual Worker runtime uses native limits, streams bytes and refuses origin redirects',async()=>{
  const script=await readFile('dist/index.js','utf8');
  let upstreamCalls=0;
  const mf=new Miniflare(convertV4MiniflareOptions({workers:[{name:'ingress',modules:true,script,compatibilityDate:'2026-11-03',bindings:{ORIGIN_URL:'https://native.invalid',ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'synthetic'},
    ratelimits:{PUBLIC_LIMIT:{namespace_id:'1',simple:{limit:100,period:60}},LOGIN_LIMIT:{namespace_id:'2',simple:{limit:1,period:60}},COSTLY_LIMIT:{namespace_id:'3',simple:{limit:1,period:60}}},
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
    const limited=await mf.dispatchFetch('https://api.invalid/v1/%61uth/%6cogin',{...init,headers:{...init.headers,authorization:'Bearer changed'}});assert.equal(limited.status,429);assert.equal(upstreamCalls,1);await limited.text();
    const response=await mf.dispatchFetch('https://api.invalid/v1/billing/webhooks/stripe',{...init,method:'POST',body:new Uint8Array([0,255,13,10])});assert.deepEqual(new Uint8Array(await response.arrayBuffer()),new Uint8Array([0,255,13,10]));
    const alias=await mf.dispatchFetch('https://api.invalid/v1/%6Aobs',{...init,method:'POST',body:'raw'});assert.equal(alias.status,200);assert.equal(await alias.text(),'raw');
    const costLimit=await mf.dispatchFetch('https://api.invalid/v1/jobs',{...init,method:'POST',body:'raw'});assert.equal(costLimit.status,429);await costLimit.text();assert.equal(upstreamCalls,3);
  } finally {await mf.dispose();}
});
test('real TCP disconnect aborts the exact forwarded signal while upstream headers are pending',async()=>{
  const config=JSON.parse((await readFile('wrangler.jsonc','utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n'));
  let script=await readFile('dist/index.js','utf8');
  let start,closed,incoming,client;
  const started=new Promise(resolve=>{start=resolve;}),cancelled=new Promise(resolve=>{closed=resolve;}),incomingAborted=new Promise(resolve=>{incoming=resolve;});
  // Observe actual runtime events without introducing an AbortController. The real
  // production proxy still constructs/forwards the Request and its signal.
  script=script.replace('index_default as default','observedFetch as default')+`
  const observedFetch={fetch(request,env,ctx){
    request.signal.addEventListener('abort',()=>ctx.waitUntil(env.REPORT.fetch('https://report.invalid/incoming')),{once:true});
    return proxyRequest(request,env,async upstream=>{
      upstream.signal.addEventListener('abort',()=>ctx.waitUntil(env.REPORT.fetch('https://report.invalid/cancelled')),{once:true});
      return fetch(upstream);
    });
  }};`;
  let mf;
  try {
  mf=new Miniflare(convertV4MiniflareOptions({workers:[{name:'ingress',modules:true,script,compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,
    unsafeDirectSockets:[{host:'127.0.0.1',port:0}],
    bindings:{ORIGIN_URL:'https://native.invalid',ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'synthetic'},
    ratelimits:Object.fromEntries(config.env.staging.ratelimits.map(({name,...binding})=>[name,binding])),
    outboundService:'pending-upstream',
    serviceBindings:{REPORT:async request=>{if(new URL(request.url).pathname==='/incoming')incoming();else closed();return new Response('ok');}},
  },{name:'pending-upstream',modules:true,compatibilityDate:config.compatibility_date,compatibilityFlags:config.compatibility_flags,
    script:`export default {async fetch(request,env){await env.REPORT.fetch('https://report.invalid/started');return new Promise(resolve=>setTimeout(()=>resolve(new Response('late')),60000));}};`,
    serviceBindings:{REPORT:async()=>{start();return new Response('ok');}},
  }]}));
  const within=async(promise,message)=>{let timer;try{const result=await Promise.race([promise.then(()=>true),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),1500);})]);assert.equal(result,true,message);}finally{clearTimeout(timer);}};
    // Connect to the user Worker's real workerd HTTP socket directly, avoiding
    // any dev-router or Node callback bridge in the disconnect observation.
    const url=await mf.unsafeGetDirectURL('ingress');url.pathname='/v1/health';
    client=httpRequest(url,{headers:{'CF-Connecting-IP':'192.0.2.75'}});client.on('error',()=>{});client.end();
    await within(started,'fixture never reached the pending upstream');client.socket.resetAndDestroy();
    await within(incomingAborted,'actual incoming request signal did not abort on disconnect');
    await within(cancelled,'the exact signal passed to pending origin fetch did not abort');
  } finally {client?.destroy();await mf?.dispose();}
});
