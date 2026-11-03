import { createHmac, timingSafeEqual } from 'node:crypto';
import Stripe from 'stripe';
import { integerText, parseExactJson } from './integers.js';

export const packs=new Map([[500,500],[1100,1000],[2400,2000]]);
const catalogs=new Map([['kenkui_credits_v1',new Map([[500,500],[1000,1000],[2000,2000]])],['kenkui_credits_v2',packs]]);
export class InvalidStripeEvent extends Error {}
const invalid=()=>new InvalidStripeEvent('invalid_payment_event');

const record=(v:unknown):v is Record<string,unknown>=>typeof v==='object'&&v!==null&&!Array.isArray(v);
function numericEquals(input:unknown,value:bigint):boolean{
  if(typeof input==='boolean')return BigInt(input)===value;
  return input===value || (typeof input==='number' && Number.isInteger(input) && BigInt(input)===value);
}
function packCredits(value:unknown):number{
  const integer=typeof value==='string'?integerText(value):typeof value==='bigint'?value:
    typeof value==='number'&&Number.isFinite(value)?BigInt(Math.trunc(value)):null;
  if(integer===null||integer<1n||integer>2147483647n)throw invalid();
  return Number(integer);
}
export class StripePayments {
  constructor(private readonly secret:string,private readonly grant:(id:string,account:string,credits:number)=>Promise<void>,
    private readonly now=()=>Date.now()/1000){if(!secret)throw Error('Missing Stripe signing secret');}
  async processStripeEvent(rawBody:Uint8Array, signature:string):Promise<void>{
    const parts=signature.split(',').map(p=>p.trim());
    const timestamps=parts.filter(p=>p.startsWith('t=')).map(p=>p.slice(2));
    const digests=parts.filter(p=>p.startsWith('v1=')).map(p=>Buffer.from(p.slice(3)));
    const timestamp=timestamps[0]??'',seconds=integerText(timestamp);
    const expected=Buffer.from(createHmac('sha256',this.secret).update(timestamp+'.').update(rawBody).digest('hex'));
    if(timestamps.length!==1||seconds===null||Math.abs(this.now()-Number(seconds))>300||
      !digests.some(d=>d.length===expected.length && timingSafeEqual(d,expected)))throw new InvalidStripeEvent('invalid_webhook_signature');
    let event:unknown;
    try {event=parseExactJson(new TextDecoder('utf-8',{fatal:true}).decode(rawBody));}catch{throw invalid();}
    if(!record(event))throw invalid();
    if(event.type!=='checkout.session.completed'&&event.type!=='checkout.session.async_payment_succeeded')return;
    const session=record(event.data)?event.data.object:null;
    if(!record(session))throw invalid();
    const metadata=session.metadata||{};
    if(!record(metadata))throw invalid();
    const catalog=typeof metadata.purpose==='string'?catalogs.get(metadata.purpose):null;
    if(!catalog||session.payment_status!=='paid')return;
    const credits=packCredits(metadata.credits),price=catalog.get(credits),totals=session.total_details;
    if(!price||!record(totals)||typeof totals.amount_tax!=='bigint'||totals.amount_tax<0n||
      !numericEquals(totals.amount_discount,0n)||!numericEquals(totals.amount_shipping,0n)||
      !numericEquals(session.amount_subtotal,BigInt(price))||!numericEquals(session.amount_total,BigInt(price)+totals.amount_tax)||
      typeof metadata.account_id!=='string'||!metadata.account_id||session.client_reference_id!==metadata.account_id||
      typeof session.id!=='string'||!session.id.startsWith('cs_')||session.mode!=='payment'||session.currency!=='usd')throw invalid();
    await this.grant(session.id,metadata.account_id,credits);
  }
}
type CreateSession=(params:Stripe.Checkout.SessionCreateParams)=>Promise<{url:string|null}>;
export class Checkout {
  private readonly origin:string;
  constructor(private readonly createSession:CreateSession,origin:string,private readonly taxCode:string){
    const url=new URL(origin);
    if(url.protocol!=='https:'||url.hash||url.search)throw Error('Invalid checkout origin');
    this.origin=origin.replace(/\/+$/,'');
  }
  static enabled(env:NodeJS.ProcessEnv):boolean {
    return Boolean(env.STRIPE_SECRET_KEY && env.STRIPE_WEBHOOK_SECRET && env.KENKUI_WEB_ORIGIN &&
      /^\d{4}-\d{2}-\d{2}(?:\.[a-z]+)?$/.test(env.KENKUI_STRIPE_API_VERSION??''));
  }
  static fromEnvironment(env:NodeJS.ProcessEnv):Checkout {
    if(!Checkout.enabled(env))throw Error('Missing checkout configuration');
    // The SDK types only its newest version. Retain the operator-verified account default used by legacy checkout.
    const stripe=new Stripe(env.STRIPE_SECRET_KEY!,{apiVersion:env.KENKUI_STRIPE_API_VERSION as Stripe.LatestApiVersion,
      timeout:20000,maxNetworkRetries:0});
    return new Checkout(p=>stripe.checkout.sessions.create(p),env.KENKUI_WEB_ORIGIN!,env.KENKUI_STRIPE_TAX_CODE??'txcd_10105001');
  }
  async create(account:string,credits:number):Promise<string>{
    const price=packs.get(credits);
    if(price===undefined)throw Error('invalid_credit_pack');
    const session=await this.createSession({mode:'payment',managed_payments:{enabled:true},client_reference_id:account,
      metadata:{account_id:account,credits:String(credits),purpose:'kenkui_credits_v2'},
      success_url:this.origin+'/billing?checkout=success',cancel_url:this.origin+'/billing?checkout=cancelled',
      line_items:[{quantity:1,price_data:{currency:'usd',unit_amount:price,tax_behavior:'exclusive',
        product_data:{tax_code:this.taxCode,name:credits.toLocaleString('en-US')+' Kenkui credits'}}}]});
    if(typeof session.url!=='string'||!session.url.startsWith('https://checkout.stripe.com/'))throw Error('invalid_checkout_response');
    return session.url;
  }
}
