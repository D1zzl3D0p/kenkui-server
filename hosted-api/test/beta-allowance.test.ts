import { expect,it,vi } from 'vitest';
import type { Pool } from 'pg';
import { BillingStore } from '../src/db/billing.js';

it('zero beta allowance retains an existing balance without inserting or changing credits',async()=>{
  vi.stubEnv('KENKUI_BETA_CREDITS','0');
  const queries:string[]=[];
  const client={query:async(sql:string)=>{
    queries.push(sql);
    if(sql.startsWith('INSERT INTO credit_accounts'))return {rows:[{id:'existing-account'}]};
    if(sql.startsWith('SELECT available_credits'))return {rows:[{available_credits:'279'}]};
    return {rows:[]};
  },release(){}};
  try {
    const service=new BillingStore(()=>({connect:async()=>client}) as unknown as Pool);
    expect(await service.accountForOwner('owner')).toEqual({id:'existing-account',availableCredits:'279'});
    expect(queries.some(sql=>sql.includes('credit_ledger_entries')||sql.includes('credit_lots')||sql.startsWith('UPDATE credit_accounts'))).toBe(false);
    expect(queries.at(-1)).toBe('COMMIT');
  } finally {vi.unstubAllEnvs();}
});
