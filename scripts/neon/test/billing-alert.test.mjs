import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { createBillingHelper, estimateLaunchCost } from '../billing-alert.mjs';

function service(responses) {
  const calls=[];
  return {calls,fetch:async(url,options)=>{
    calls.push({url,options});const next=responses.shift();
    if(next instanceof Error)throw next;
    return new Response(JSON.stringify(next?.body??next),{status:next?.status??200});
  }};
}
const config={orgId:'org-reviewed',plan:'Launch',role:'admin',principalId:'synthetic-admin',apiKey:'synthetic-private-key',
  adminAttestation:{orgId:'org-reviewed',principalId:'synthetic-admin',credentialSha256:createHash('sha256').update('synthetic-private-key').digest('hex'),role:'admin',approvalRef:'synthetic-independent-review',attestedBy:'synthetic-operator',attestedAt:new Date().toISOString()}};
test('documented organization PUT is bounded by paid/admin/exact scope and independent GET readback',async()=>{
  const s=service([{spending_limit_cents:null},{spending_limit_cents:500},{spending_limit_cents:500}]);
  assert.equal(await createBillingHelper(config,s.fetch).setSpendingAlert('org-reviewed',500),500);
  assert.deepEqual(s.calls.map(c=>[c.url,c.options.method]),[
    ['https://console.neon.tech/api/v2/organizations/org-reviewed/billing/spending_limit','GET'],
    ['https://console.neon.tech/api/v2/organizations/org-reviewed/billing/spending_limit','PUT'],
    ['https://console.neon.tech/api/v2/organizations/org-reviewed/billing/spending_limit','GET'],
  ]);
  assert.deepEqual(JSON.parse(s.calls[1].options.body),{spending_limit_cents:500});
  assert(s.calls.every(c=>c.options.redirect==='error'&&c.options.signal));
});
test('Free, missing admin, org mismatch and invalid amounts cannot write or activate a plan',async()=>{
  for(const [selected,id,cents] of [[{...config,plan:'Free'},config.orgId,500],[{...config,role:'member'},config.orgId,500],[config,'other-org',500],...[0,null,-1,1.5,1,499,501,10000,Number.MAX_SAFE_INTEGER+1].map(n=>[config,config.orgId,n])]) {
    const s=service([]);await assert.rejects(()=>createBillingHelper(selected,s.fetch).setSpendingAlert(id,cents));assert.equal(s.calls.length,0);
  }
});
test('paid read access cannot substitute for independent credential/principal-bound admin review',async()=>{
  for(const selected of [{...config,adminAttestation:undefined},{...config,principalId:'read-only-principal'},{...config,apiKey:'different-key'},{...config,adminAttestation:{...config.adminAttestation,role:'member'}}]) {
    const s=service([{spending_limit_cents:null},{spending_limit_cents:500},{spending_limit_cents:500}]);
    await assert.rejects(()=>createBillingHelper(selected,s.fetch).setSpendingAlert(config.orgId,500));assert.equal(s.calls.length,0);
  }
});
test('readback disagreement, forbidden preflight and provider failures stay sanitized without retry',async()=>{
  for(const responses of [
    [{spending_limit_cents:null},{spending_limit_cents:500},{spending_limit_cents:400}],
    [{status:403,body:{message:config.apiKey}}],
    [new Error(config.apiKey)],
    [{spending_limit_cents:null},new Error(config.apiKey)],
  ]) {
    const s=service(responses);await assert.rejects(()=>createBillingHelper(config,s.fetch).setSpendingAlert(config.orgId,500),e=>!e.message.includes(config.apiKey));
    assert(s.calls.filter(c=>c.options.method==='PUT').length<=1);
  }
});
test('Launch illustrations include separately metered usage; unknown actual usage is not zero',()=>{
  assert.throws(()=>estimateLaunchCost({}));
  assert.deepEqual(estimateLaunchCost({cuHours:180,storageGbMonths:0,historyGbMonths:0,functionActiveHours:0,functionWaitingHours:0,functionInvocations:0}),
    {databaseCompute:19.08,databaseStorage:0,history:0,functionActive:0,functionWaiting:0,functionInvocations:0,subtotal:19.08});
  const usage=estimateLaunchCost({cuHours:1,storageGbMonths:2,historyGbMonths:3,functionActiveHours:4,functionWaitingHours:5,functionInvocations:1_000_000});
  assert.equal(usage.subtotal,2.531);assert.throws(()=>estimateLaunchCost({cuHours:-1}));
});
