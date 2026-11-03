import { describe,expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { registerAuthRoutes } from '../src/auth/pages.js';
import type { LegacyRecovery } from '../src/auth/recovery.js';

function setup(recovery?:LegacyRecovery) {
 const config={issuer:'https://branch.invalid/db/auth',jwksUrl:'https://branch.invalid/db/auth/.well-known/jwks.json',resource:'https://api.invalid',nativeClientId:'desktop',allowedOrigins:['https://app.invalid']};
 const getPool=vi.fn(()=>{throw new Error('Unexpected DB');});const auth={verify:vi.fn(async()=>({userId:'owner',issuer:config.issuer,subject:'user'}))};
 const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool,auth});registerAuthRoutes(app,config,{browserSource:'/* compiled synthetic asset */',...(recovery?{recovery}:{})});return {app,auth,getPool};
}
describe('hosted auth routes',()=>{
 it('serves Google/GitHub login and protected consent pages without DB/auth calls',async()=>{
   const {app,auth,getPool}=setup();
   for(const path of ['login','consent']) {const res=await app.request('/v1/auth/'+path+'?signed=query');expect(res.status).toBe(200);expect(res.headers.get('Content-Security-Policy')).toContain("frame-ancestors 'none'");expect(res.headers.get('Cache-Control')).toBe('no-store');expect(res.headers.get('Referrer-Policy')).toBe('no-referrer');}
   const login=await (await app.request('/v1/auth/login')).text();expect(login).toContain('Google');expect(login).toContain('GitHub');expect(login).not.toContain('password');
   const asset=await app.request('/v1/auth/client.js');expect(asset.status).toBe(200);expect(asset.headers.get('Content-Type')).toContain('javascript');expect(auth.verify).not.toHaveBeenCalled();expect(getPool).not.toHaveBeenCalled();
 });
 it('publishes v2 metadata, forces old native clients to upgrade and preserves session userId',async()=>{
   const {app}=setup();const config=await (await app.request('/v1/auth/native/config')).json();expect(config).toMatchObject({protocolVersion:2,redirectUri:'http://127.0.0.1:43827/callback',clientId:'desktop'});
   for(const path of ['authorize','exchange','refresh','logout']) {const res=await app.request('/v1/auth/native/'+path,{method:path==='authorize'?'GET':'POST'});expect(res.status).toBe(426);expect(res.headers.get('Cache-Control')).toBe('no-store');}
   const headers={Authorization:'Bearer candidate'};expect(await (await app.request('/v1/auth/session',{headers})).json()).toEqual({userId:'owner'});
   expect(await (await app.request('/v1/auth/logout',{method:'POST',headers})).json()).toEqual({protocolVersion:2,managedSignOutRequired:true});
   const failed=await app.request('/v1/auth/session');expect(failed.status).toBe(401);expect(failed.headers.get('Cache-Control')).toBe('no-store');
 });
 it('refuses HTTP ownership/confirmation fields and bounds chunked recovery input',async()=>{
   const link=vi.fn(async()=>({userId:'owner'}));const {app}=setup({link} as unknown as LegacyRecovery);
   for(const body of [{legacyAccessToken:'jwt',userId:'victim'},{legacyAccessToken:'jwt',issuer:'other'},{legacyAccessToken:'jwt',legacyAccountCheck:'confirmed-new'},{email:'old@invalid'}]) expect((await app.request('/v1/auth/migration/link',{method:'POST',body:JSON.stringify(body)})).status).toBe(422);
   expect((await app.request('/v1/auth/migration/link',{method:'POST',body:'x'.repeat(18_001)})).status).toBe(413);expect(link).not.toHaveBeenCalled();
   expect((await app.request('/v1/auth/migration/link',{method:'POST',body:JSON.stringify({legacyAccessToken:'jwt'})})).status).toBe(200);
 });
});
