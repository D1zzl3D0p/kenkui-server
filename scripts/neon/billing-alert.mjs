import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const endpoint='https://console.neon.tech/api/v2/organizations/';
/** Reviewed operator context is required; this helper cannot activate or change a plan. */
export function createBillingHelper(config,fetchImpl=fetch) {
  return {async setSpendingAlert(orgId,cents) {
    if(!config||orgId!==config.orgId||!/^[a-z0-9-]{1,60}$/.test(orgId)||!['Launch','Scale'].includes(config.plan)||config.role!=='admin'||
      typeof config.apiKey!=='string'||!config.apiKey.trim()||cents!==500)throw Error('Billing scope, paid-plan, admin or amount review required');
    const a=config.adminAttestation,time=Date.parse(a?.attestedAt);
    if(!a||a.orgId!==orgId||a.principalId!==config.principalId||typeof config.principalId!=='string'||!config.principalId.trim()||a.role!=='admin'||
      a.credentialSha256!==createHash('sha256').update(config.apiKey).digest('hex')||!a.approvalRef?.trim()||!a.attestedBy?.trim()||!Number.isFinite(time)||time>Date.now())throw Error('Independent principal-bound organization admin review required');
    const url=endpoint+orgId+'/billing/spending_limit';
    async function request(method) {
      try {
        const response=await fetchImpl(url,{method,redirect:'error',signal:AbortSignal.timeout(15_000),
          headers:{Authorization:'Bearer '+config.apiKey,Accept:'application/json',...(method==='PUT'?{'Content-Type':'application/json'}:{})},
          ...(method==='PUT'?{body:JSON.stringify({spending_limit_cents:cents})}:{})});
        if(response.status!==200)throw Error();
        const value=(await response.json()).spending_limit_cents;
        if(value!==null&&(!Number.isSafeInteger(value)||value<1))throw Error();
        return value;
      } catch {throw Error('Billing request failed; read provider state before any manual retry');}
    }
    // GET proves paid-tier read access only; the independent admin attestation above
    // is separately bound to the chosen principal and credential. PUT enforces live admin rights.
    await request('GET');
    if(await request('PUT')!==cents)throw Error('Billing write response mismatch; read provider state');
    if(await request('GET')!==cents)throw Error('Billing readback mismatch; alert is unverified');
    return cents;
  }};
}

/** Nov 3 2026 Launch list rates; actual usage and additional charges are separate evidence. */
export function estimateLaunchCost(usage) {
  const rates={cuHours:['databaseCompute',0.106],storageGbMonths:['databaseStorage',0.35],historyGbMonths:['history',0.20],
    functionActiveHours:['functionActive',0.10],functionWaitingHours:['functionWaiting',0.025],functionInvocations:['functionInvocations',0.60/1_000_000]};
  const result={};
  for(const [input,[output,rate]] of Object.entries(rates)) {
    if(typeof usage?.[input]!=='number'||!Number.isFinite(usage[input])||usage[input]<0)throw Error('Complete nonnegative measured usage required');
    result[output]=Math.round(usage[input]*rate*1e9)/1e9;
  }
  result.subtotal=Math.round(Object.values(result).reduce((a,b)=>a+b,0)*1e9)/1e9;return result;
}

// No default write. A separately reviewed paid-plan/admin/scope receipt and explicit apply are mandatory.
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const [action,path,orgId,...extra]=process.argv.slice(2);
    if(action!=='apply-reviewed-500'||!path||!orgId||extra.length)throw Error();
    const config=JSON.parse(await readFile(path,'utf8'));
    const helper=createBillingHelper({...config,apiKey:process.env.NEON_BILLING_API_KEY});
    const cents=await helper.setSpendingAlert(orgId,500);
    console.log(JSON.stringify({spending_limit_cents:cents,verified:true,action:'notification-only'}));
  } catch {console.error('Billing change unverified; check reviewed scope and provider state privately.');process.exitCode=1;}
}
