import type { Hono } from 'hono';
import { getOnly } from './get-only.js';
import { InvalidStripeEvent, packs, type Checkout, type StripePayments } from '../billing/stripe.js';
import { JsonIntegerTooLong, parseExactJson } from '../billing/integers.js';
import { PaymentReferenceConflict, type BillingStore } from '../db/billing.js';
import { ApiError, type ApiEnv } from '../types.js';

interface BillingServices {
  store:Pick<BillingStore,'accountForOwner'|'history'>;
  checkoutEnabled:boolean;checkout:()=>Pick<Checkout,'create'>;
  webhook?:Pick<StripePayments,'processStripeEvent'>;
}
type ValidationIssue={type:string;loc:(string|number)[];msg:string;input:unknown;ctx?:Record<string,unknown>};
const validation=(errors:ValidationIssue[])=>new ApiError(422,'validation_error','Request validation failed',{errors});
const parserReasons=new Map([
  ["Expected property name or '}' in JSON",'Expecting property name enclosed in double quotes'],
  ['Expected double-quoted property name in JSON','Expecting property name enclosed in double quotes'],
  ["Expected ':' after property name in JSON","Expecting ':' delimiter"],
  ['Unexpected non-whitespace character after JSON','Extra data'],
]);
function invalidJson(error:unknown,raw:string):ApiError {
  // Node sometimes includes request text in a SyntaxError. Only translate exact known diagnostics.
  const match=error instanceof SyntaxError?/^(.+) at position (\d+) \(line \d+ column \d+\)$/.exec(error.message):null;
  const reason=match&&parserReasons.get(match[1]!);
  let position=0;
  if(reason){
    // V8 counts UTF-16 units, Python counts code points.
    for(const _character of raw.slice(0,Number(match![2])))position++;
  }
  return validation([{type:'json_invalid',loc:['body',position],msg:'JSON decode error',input:{},ctx:{error:reason||'Expecting value'}}]);
}
function checkoutCredits(raw:string,contentType:string|undefined):bigint {
  let value:unknown=raw,original:unknown=raw;
  const mime=contentType?.split(';')[0]?.trim().toLowerCase();
  const isJson=!mime||/^application\/(?:json|[\w.+-]+\+json)$/.test(mime);
  try {
    if(raw&&isJson){value=parseExactJson(raw);original=JSON.parse(raw);}
  }
  catch(error){
    if(error instanceof JsonIntegerTooLong)throw new ApiError(400,'http_error','There was an error parsing the body');
    throw invalidJson(error,raw);
  }
  if(!raw||value===null)throw validation([{type:'missing',loc:['body'],msg:'Field required',input:null}]);
  if(typeof value!=='object'||Array.isArray(value))
    throw validation([{type:'model_attributes_type',loc:['body'],msg:'Input should be a valid dictionary or object to extract fields from',input:original}]);
  const payload=value as Record<string,unknown>,input=original as Record<string,unknown>,errors:ValidationIssue[]=[];
  if(!Object.hasOwn(payload,'credits'))errors.push({type:'missing',loc:['body','credits'],msg:'Field required',input});
  else if(typeof payload.credits!=='bigint')errors.push({type:'int_type',loc:['body','credits'],msg:'Input should be a valid integer',input:input.credits});
  for(const key of Object.keys(payload))if(key!=='credits')errors.push({type:'extra_forbidden',loc:['body',key],msg:'Extra inputs are not permitted',input:input[key]});
  if(errors.length)throw validation(errors);
  return payload.credits as bigint;
}
export function registerBillingRoutes(app:Hono<ApiEnv>,services:BillingServices):void {
  const account=(owner:string)=>services.store.accountForOwner(owner);
  app.get('/v1/billing',getOnly,async c=>{
    const result=await account(c.get('principal').userId);
    return c.json({mode:'credits',availableCredits:result.availableCredits,checkoutEnabled:String(services.checkoutEnabled),
      creditHistoryAvailable:true,packs:Array.from(packs,([credits,priceUsdCents])=>({credits,priceUsdCents}))});
  });
  app.get('/v1/billing/history',getOnly,async c=>{
    const result=await account(c.get('principal').userId);
    return c.json({items:await services.store.history(result.id)});
  });
  app.post('/v1/billing/checkout',async c=>{
    const amount=checkoutCredits(await c.req.text(),c.req.header('Content-Type'));
    if(!services.checkoutEnabled)throw new ApiError(503,'http_error','Card payments are not configured yet.');
    const credits=Number(amount);
    if(!packs.has(credits))throw new ApiError(422,'http_error','invalid_credit_pack');
    const result=await account(c.get('principal').userId);
    try{return c.json({url:await services.checkout().create(result.id,credits)});}
    catch{throw new ApiError(502,'http_error','Unable to start checkout. Please try again.');}
  });
  const webhook=services.webhook;
  if(webhook)app.post('/v1/billing/webhooks/stripe',async c=>{
    const signature=c.req.header('Stripe-Signature');
    if(!signature)return c.body(null,400);
    try {await webhook.processStripeEvent(new Uint8Array(await c.req.arrayBuffer()),signature);}
    catch(error){
      if(error instanceof InvalidStripeEvent||error instanceof PaymentReferenceConflict)return c.body(null,400);
      throw error;
    }
    return c.body(null,204);
  });
}
