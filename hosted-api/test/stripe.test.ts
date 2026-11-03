import Stripe from 'stripe';
import { createHmac } from 'node:crypto';
import { expect, it, vi } from 'vitest';
import { StripePayments, Checkout } from '../src/billing/stripe.js';

const now=123456, secret='synthetic-signing-secret', account='account';
export function paid(accountId=account,credits=1100,price=1000) {
  return {type:'checkout.session.completed',data:{object:{id:'cs_fixture',mode:'payment',currency:'usd',
    payment_status:'paid',client_reference_id:accountId,metadata:{account_id:accountId,purpose:'kenkui_credits_v2',credits:String(credits)},
    amount_subtotal:price,amount_total:price+23,total_details:{amount_tax:23,amount_shipping:0,amount_discount:0}}}};
}
function sign(raw:Uint8Array,t=String(now)){
  return 't='+t+',v1=wrong,v1='+createHmac('sha256',secret).update(t+'.').update(raw).digest('hex');
}
it('authenticates original bytes, signed timestamp text, rotation, and both tolerance boundaries',async()=>{
  const grant=vi.fn(), handler=new StripePayments(secret,grant,()=>now);
  const raw=Buffer.from(JSON.stringify(paid(),null,2));
  for(const delta of [-300,0,300])await handler.processStripeEvent(raw,sign(raw,'0'+(now+delta)));
  expect(grant.mock.calls).toEqual(Array(3).fill(['cs_fixture',account,1100]));
  for(const bad of [sign(raw,String(now+301)),sign(raw,String(now-301)),sign(raw)+',t='+now,sign(raw).toUpperCase()])
    await expect(handler.processStripeEvent(raw,bad)).rejects.toThrow('invalid_webhook_signature');
  await expect(handler.processStripeEvent(Buffer.from(JSON.stringify(paid())),sign(raw))).rejects.toThrow('invalid_webhook_signature');
  expect(grant).toHaveBeenCalledTimes(3);
});
it('accepts legacy and async purchases, ignores unpaid/unknown purpose, rejects altered economics',async()=>{
  const grant=vi.fn(),handler=new StripePayments(secret,grant,()=>now);
  async function send(e:unknown){const raw=Buffer.from(JSON.stringify(e));return handler.processStripeEvent(raw,sign(raw));}
  const legacy=paid(account,1000);legacy.data.object.metadata.purpose='kenkui_credits_v1';legacy.type='checkout.session.async_payment_succeeded';
  await send(legacy); expect(grant).toHaveBeenCalledWith('cs_fixture',account,1000);
  for(const patch of [{payment_status:'unpaid'},{metadata:{purpose:'constructor'}}])await send({...legacy,data:{object:{...legacy.data.object,...patch}}});
  for(const patch of [{id:'evt_wrong'},{client_reference_id:'other'},{amount_total:1000},{amount_subtotal:999},{currency:'eur'},{mode:'subscription'},
    {metadata:{...legacy.data.object.metadata,credits:'0x3e8'}},...['amount_tax','amount_shipping','amount_discount'].map(k=>({total_details:{...legacy.data.object.total_details,[k]:-1}}))]){
    await expect(send({...legacy,data:{object:{...legacy.data.object,...patch}}})).rejects.toThrow('invalid_payment_event');
  }
  for(const token of ['23.0','2.3e1','true']){
    const raw=Buffer.from(JSON.stringify(legacy).replace('"amount_tax":23','"amount_tax":'+token));
    await expect(handler.processStripeEvent(raw,sign(raw))).rejects.toThrow('invalid_payment_event');
  }
  expect(grant).toHaveBeenCalledTimes(1);
});
it('requires a deployment-pinned account API version and passes it to the official SDK',async()=>{
  const env={STRIPE_SECRET_KEY:'sk_test_synthetic',STRIPE_WEBHOOK_SECRET:'synthetic',KENKUI_WEB_ORIGIN:'https://web.invalid'};
  // Replace the actual SDK transport before constructing any checkout client.
  const transport=vi.spyOn(Object.getPrototypeOf(Stripe.createNodeHttpClient()),'makeRequest').mockResolvedValue({
    getStatusCode:()=>200,getHeaders:()=>({}),getRawResponse:()=>({}),toJSON:async()=>({url:'https://checkout.stripe.com/fixture'}),
  });
  try {
    expect(()=>Checkout.fromEnvironment(env)).toThrow();expect(transport).not.toHaveBeenCalled();
    const checkout=Checkout.fromEnvironment({...env,KENKUI_STRIPE_API_VERSION:'2025-06-30.basil'});
    expect(await checkout.create('internal',500)).toBe('https://checkout.stripe.com/fixture');
    const wire=transport.mock.calls[0]!;
    expect(wire.slice(0,4)).toEqual(['api.stripe.com','443','/v1/checkout/sessions','POST']);
    expect(new Headers(wire[4] as HeadersInit).get('Stripe-Version')).toBe('2025-06-30.basil');
    expect(new URLSearchParams(wire[5] as string).get('managed_payments[enabled]')).toBe('true');
  } finally {transport.mockRestore();}
});
it('creates a server-priced managed session tied to the internal account',async()=>{
  const create=vi.fn().mockResolvedValue({url:'https://checkout.stripe.com/c/pay/test'});
  const checkout=new Checkout(create,'https://example.invalid/','txcd_10105001');
  await expect(checkout.create(account,1100)).resolves.toBe('https://checkout.stripe.com/c/pay/test');
  expect(create).toHaveBeenCalledWith({mode:'payment',managed_payments:{enabled:true},client_reference_id:account,
    metadata:{account_id:account,credits:'1100',purpose:'kenkui_credits_v2'},success_url:'https://example.invalid/billing?checkout=success',
    cancel_url:'https://example.invalid/billing?checkout=cancelled',line_items:[{quantity:1,price_data:{currency:'usd',unit_amount:1000,tax_behavior:'exclusive',
    product_data:{tax_code:'txcd_10105001',name:'1,100 Kenkui credits'}}}]});
  create.mockResolvedValue({url:'https://checkout.stripe.com.evil.invalid/'});
  await expect(checkout.create(account,2400)).rejects.toThrow();
});
