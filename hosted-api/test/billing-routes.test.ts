import { createHmac } from 'node:crypto';
import { expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { StripePayments } from '../src/billing/stripe.js';
import { registerBillingRoutes } from '../src/routes/billing.js';
import { R2ObjectStore } from '../src/storage/r2.js';
import type { S3Client } from '@aws-sdk/client-s3';

const config=loadConfig({KENKUI_ENV:'staging'}),headers={Authorization:'Bearer fixture','Content-Type':'application/json'};
function setup(){
  const app=createApi({config,getPool(){throw Error('unexpected');},auth:{async verify(){return {issuer:'iss',subject:'sub',userId:'owner'};}}});
  const accountForOwner=vi.fn().mockResolvedValue({id:'internal',availableCredits:'9007199254740993'});
  const history=vi.fn().mockResolvedValue([{id:'lot'}]),create=vi.fn().mockResolvedValue('https://checkout.stripe.com/fixture');
  const grant=vi.fn().mockResolvedValue(undefined);
  const webhook=new StripePayments('test-secret',grant,()=>100);
  const services={store:{accountForOwner,history},checkout:()=>({create}),checkoutEnabled:true,webhook};
  registerBillingRoutes(app,services);
  return {app,accountForOwner,history,create,grant,services};
}
it.each(['/v1/billing','/v1/billing/history'])('rejects HEAD %s after auth without resolving an account',async path=>{
  const {app,accountForOwner,history}=setup();
  expect((await app.request(path,{method:'HEAD'})).status).toBe(401);
  expect((await app.request(path,{method:'HEAD',headers})).status).toBe(405);
  expect((await app.request('/v1/billing/absent',{method:'HEAD',headers})).status).toBe(404);
  expect(accountForOwner).not.toHaveBeenCalled();expect(history).not.toHaveBeenCalled();
});
it('bounds signed JSON integer tokens at the Python 4300-digit limit before checkout',async()=>{
  const {app,accountForOwner,create}=setup();
  for(const sign of ['','-']){
    const post=(digits:number)=>app.request('/v1/billing/checkout',{method:'POST',headers,body:'{"credits":'+sign+'9'.repeat(digits)+'}'});
    const allowed=await post(4300);expect((await allowed.json()).error.message).toBe('invalid_credit_pack');
    const rejected=await post(4301);expect(rejected.status).toBe(400);
    expect((await rejected.json()).error).toMatchObject({code:'http_error',message:'There was an error parsing the body'});
  }
  expect(accountForOwner).not.toHaveBeenCalled();expect(create).not.toHaveBeenCalled();
});
it('reports safe Python-style locations for native parser errors with known positions',async()=>{
  const {app,create}=setup();
  for(const [body,offset,reason] of [
    ['{',1,'Expecting property name enclosed in double quotes'],
    ['{"x" 4}',5,"Expecting ':' delimiter"],
    ['{"x":"😀"} trailing private',10,'Extra data'],
  ] as const){
    const res=await app.request('/v1/billing/checkout',{method:'POST',headers,body});
    expect(res.status).toBe(422);
    const errors=(await res.json()).error.details.errors;
    expect(errors).toEqual([{type:'json_invalid',loc:['body',offset],msg:'JSON decode error',input:{},ctx:{error:reason}}]);
  }
  expect(create).not.toHaveBeenCalled();
});
it('uses resolved internal account, decimal balance, string feature flag and strict server catalog',async()=>{
  const {app,accountForOwner,history,create,services}=setup();
  expect(await (await app.request('/v1/billing',{headers})).json()).toEqual({mode:'credits',availableCredits:'9007199254740993',
    checkoutEnabled:'true',creditHistoryAvailable:true,packs:[{credits:500,priceUsdCents:500},{credits:1100,priceUsdCents:1000},{credits:2400,priceUsdCents:2000}]});
  expect(accountForOwner).toHaveBeenCalledWith('owner');
  expect(await (await app.request('/v1/billing/history',{headers})).json()).toEqual({items:[{id:'lot'}]});expect(history).toHaveBeenCalledWith('internal');
  const post=(body:string)=>app.request('/v1/billing/checkout',{method:'POST',headers,body});
  for(const bad of ['{"credits":"1100"}','{"credits":1100.0}','{"credits":true}','{}','{"credits":500,"account":"other"}']) {
    const res=await post(bad);expect(res.status).toBe(422);expect((await res.json()).error.code).toBe('validation_error');
  }
  for(const body of ['', 'null']){
    const res=await post(body);expect((await res.json()).error.details.errors).toEqual([{type:'missing',loc:['body'],msg:'Field required',input:null}]);
  }
  const text=await app.request('/v1/billing/checkout',{method:'POST',headers:{...headers,'Content-Type':'text/plain'},body:'{"credits":1100}'});
  expect(text.status).toBe(422);expect((await text.json()).error.details.errors[0].type).toBe('model_attributes_type');
  expect((await (await post('{"credits":1000}')).json()).error.message).toBe('invalid_credit_pack');
  expect(await (await post('{"credits":1100}')).json()).toEqual({url:'https://checkout.stripe.com/fixture'});
  expect(create).toHaveBeenCalledWith('internal',1100);
  create.mockRejectedValue(Error('private'));expect((await post('{"credits":1100}')).status).toBe(502);
  services.checkoutEnabled=false;expect((await post('{"credits":500}')).status).toBe(503);
});
it('webhook waits for durable write, rejects changed bytes without DB access and propagates DB failures',async()=>{
  const {app,grant}=setup();
  const body=JSON.stringify({type:'checkout.session.completed',data:{object:{id:'cs_fixture',client_reference_id:'internal',payment_status:'paid',
    metadata:{account_id:'internal',purpose:'kenkui_credits_v2',credits:'500'},mode:'payment',currency:'usd',
    amount_subtotal:500,amount_total:501,total_details:{amount_tax:1,amount_discount:0,amount_shipping:0}}}},null,2);
  const signature='t=100,v1='+createHmac('sha256','test-secret').update('100.'+body).digest('hex');
  const post=(raw:string)=>app.request('/v1/billing/webhooks/stripe',{method:'POST',headers:{'Stripe-Signature':signature},body:raw});
  expect((await post(body+' ')).status).toBe(400);expect(grant).not.toHaveBeenCalled();
  const started=Promise.withResolvers<void>(),commit=Promise.withResolvers<void>();
  grant.mockImplementationOnce(()=>{started.resolve();return commit.promise;});
  let acknowledged=false;const pending=Promise.resolve(post(body)).then(r=>{acknowledged=true;return r;});
  await started.promise;expect(acknowledged).toBe(false);commit.resolve();
  const ack=await pending;expect(ack.status).toBe(204);expect(await ack.text()).toBe('');
  grant.mockRejectedValue(Error('SQL failed'));expect((await post(body)).status).toBe(500);
});
it('writes empty unsalted deployment-scoped recovery objects and validates canonical IDs',async()=>{
  const send=vi.fn().mockResolvedValue({}),id='11111111-1111-4111-8111-11111111111a';
  const store=new R2ObjectStore({endpoint:'https://example.invalid',bucket:'private',accessKeyId:'test',secretAccessKey:'test',keySalt:'unused'},{send} as unknown as S3Client);
  const signals=store.recoverySignals('staging');await signals.arm(id);await signals.clear(id);
  const key='operations/recovery/staging/'+id;
  expect(send.mock.calls.map(c=>[c[0].constructor.name,c[0].input])).toEqual([['PutObjectCommand',{Bucket:'private',Key:key,Body:Buffer.alloc(0)}],['DeleteObjectCommand',{Bucket:'private',Key:key}]]);
  await expect(signals.arm(id.toUpperCase())).rejects.toThrow();
  expect(()=>store.recoverySignals('wrong' as 'staging')).toThrow();
});
