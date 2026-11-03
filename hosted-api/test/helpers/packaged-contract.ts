// Bundled only by the rehearsal, never copied into a release archive.
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createHmac, randomUUID } from 'node:crypto';
import { Pool } from 'pg';
import { generateKeyPair, SignJWT } from 'jose';
import { createApi } from '../../src/app.js';
import { loadConfig } from '../../src/config.js';
import { createNeonAuth } from '../../src/auth/neon.js';
import { IdentityStore } from '../../src/db/identities.js';
import { R2ObjectStore } from '../../src/storage/r2.js';
import { StripePayments, Checkout } from '../../src/billing/stripe.js';
import { registerAssetRoutes } from '../../src/routes/assets.js';
import { serveApi } from '../../src/standalone.js';
import type { AssetServices } from '../../src/routes/assets.js';

const testUrl=process.env.KENKUI_TEST_DATABASE_URL;
assert.equal(testUrl,'postgresql://postgres@127.0.0.1:55438/postgres');
const schema='release_smoke_'+randomUUID().replaceAll('-',''),pool=new Pool({connectionString:testUrl,ssl:false,options:'-c search_path='+schema});
const issuer='https://auth.synthetic.invalid/db/auth',owner=randomUUID(),subject=randomUUID();
const {privateKey,publicKey}=await generateKeyPair('EdDSA');
const auth=createNeonAuth({issuer,jwksUrl:issuer+'/jwks',resource:'https://api.invalid',nativeClientId:'public-client',allowedOrigins:['https://studio.invalid']},new IdentityStore(()=>pool,'staging'),{keySet:async()=>publicKey});
const token=await new SignJWT({client_id:'public-client'}).setSubject(subject).setIssuer(issuer).setAudience('https://api.invalid').setIssuedAt().setExpirationTime('5m').setProtectedHeader({alg:'EdDSA'}).sign(privateKey);
const config=loadConfig({KENKUI_ENV:'staging',KENKUI_ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'origin-synthetic'});config.catalog.capabilities.maxUploadBytes=4;
const app=createApi({config,getPool:()=>pool,auth});
const objects=new R2ObjectStore({endpoint:'https://r2.synthetic.invalid',bucket:'synthetic',keySalt:'synthetic',accessKeyId:'synthetic-id',secretAccessKey:'synthetic-key'});
const grant=new StripePayments('synthetic-webhook',async()=>{throw Error('unexpected payment');});
// Instantiate the real Stripe SDK, without making a provider request.
Checkout.fromEnvironment({STRIPE_SECRET_KEY:'sk_test_synthetic',STRIPE_WEBHOOK_SECRET:'synthetic',KENKUI_WEB_ORIGIN:'https://studio.invalid',KENKUI_STRIPE_API_VERSION:'2026-10-01.dahlia'});
app.get('/v1/jobs/identity',c=>c.json(c.get('principal')));
app.get('/v1/jobs/sign',async()=>Response.json({url:await objects.artifactUrl(owner,'Synthetic.m4b')}));
app.post('/v1/billing/webhooks/stripe',async c=>{await grant.processStripeEvent(new Uint8Array(await c.req.arrayBuffer()),c.req.header('stripe-signature')??'');return c.json({received:true});});
app.get('/cookies',()=>{const headers=new Headers();headers.append('set-cookie','a=1; Secure; HttpOnly');headers.append('set-cookie','b=2; Secure; HttpOnly');return new Response('cookies',{headers});});
let cancelled=0;
for(const [path,type] of [['/v1/jobs/events','text/event-stream'],['/v1/jobs/download','audio/mp4']])app.get(path!,()=>new Response(new ReadableStream<Uint8Array>({start(c){c.enqueue(new TextEncoder().encode('id: 9007199254740993\n\n'));},cancel(){cancelled++;}},{highWaterMark:0}),{headers:{'content-type':type!,'cache-control':'no-store'}}));
registerAssetRoutes(app,config,()=>{throw Error('oversized body reached object/DB services');});
const headers={'x-kenkui-origin-secret':'origin-synthetic',authorization:'Bearer '+token,origin:'https://studio.invalid'};
try {
  await pool.query('CREATE SCHEMA '+schema);
  await pool.query('CREATE TABLE provider_identities (issuer TEXT,subject TEXT,identity_id UUID)');
  await pool.query('INSERT INTO provider_identities VALUES ($1,$2,$3)',[issuer,subject,owner]);
  const server=serveApi(app,{hostname:'127.0.0.1',port:0});await once(server,'listening');const addr=server.address();assert(addr&&typeof addr==='object');
  try {
    const transports=[(path:string,init:RequestInit={})=>app.fetch(new Request('https://api.invalid'+path,init)),(path:string,init:RequestInit={})=>fetch(`http://127.0.0.1:${addr.port}`+path,init)];
    for(const send of transports) {
      assert.deepEqual(await (await send('/v1/jobs/identity',{headers})).json(),{userId:owner,issuer,subject});
      assert.equal((await send('/v1/jobs/identity',{headers:{...headers,authorization:'Bearer forged'}})).status,401);
      assert.equal((await send('/v1/health')).status,403);
      assert.equal((await send('/v1/health',{headers:{...headers,'x-kenkui-origin-secret':'wrong'}})).status,403);
      const signed=await (await send('/v1/jobs/sign',{headers})).json() as {url:string};assert.equal(new URL(signed.url).hostname,'r2.synthetic.invalid');assert(new URL(signed.url).searchParams.has('X-Amz-Signature'));
      const body=' { "type" : "synthetic.unhandled", "raw": "é" }\n',stamp=String(Math.floor(Date.now()/1000)),sig=createHmac('sha256','synthetic-webhook').update(stamp+'.').update(body).digest('hex');
      assert.equal((await send('/v1/billing/webhooks/stripe',{method:'POST',body,headers:{...headers,'stripe-signature':`t=${stamp},v1=${sig}`}})).status,200);
      assert.equal((await send('/v1/assets',{method:'POST',body:'oversized',headers:{...headers,'content-type':'application/epub+zip'}})).status,413);
      const cookies=await send('/cookies',{headers});assert.equal(cookies.headers.getSetCookie().length,2);await cookies.text();
      const cors=await send('/v1/jobs/identity',{method:'OPTIONS',headers:{...headers,'Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'Authorization'}});assert.equal(cors.headers.get('access-control-allow-origin'),'https://studio.invalid');await cors.text();
      for(const path of ['/v1/jobs/events','/v1/jobs/download']) {
        const abort=new AbortController(),response=await send(path,{headers,signal:abort.signal}),reader=response.body!.getReader();assert.match(new TextDecoder().decode((await reader.read()).value),/9007199254740993/);
        await reader.cancel();abort.abort();
      }
    }
    for(let i=0;i<100&&cancelled<4;i++)await new Promise(r=>setTimeout(r,10));assert.equal(cancelled,4);
  } finally {server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
} finally {await pool.query('DROP SCHEMA IF EXISTS '+schema+' CASCADE');await pool.end();}
console.log('packaged contracts: Function + standalone JWT/PG/Stripe/R2/upload/cookies/CORS/SSE/download/abort passed');
