import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const auth=vi.hoisted(()=>({resume:vi.fn(async()=>null),continueLogin:vi.fn(async()=>{}),clientDetails:vi.fn(async()=>({data:{client_name:'Kenkui'}})),login:vi.fn(),signOut:vi.fn(),consent:vi.fn()}));
vi.mock('../src/auth/browser.js',()=>({createBrowserAuth:()=>auth}));
beforeEach(()=>{vi.clearAllMocks();vi.resetModules();});
afterEach(()=>vi.unstubAllGlobals());
function page(search:string) {
 const status={textContent:''};
 vi.stubGlobal('document',{visibilityState:'visible',body:{dataset:{page:'login',issuer:'https://auth.invalid/db/auth'}},querySelector:(s:string)=>s==='#status'?status:null,querySelectorAll:()=>[],addEventListener:vi.fn()});
 vi.stubGlobal('location',new URL('https://api.invalid/v1/auth/login'+search));
 return status;
}
it('restores ordinary social-login verifier callbacks without trying OAuth-provider continuation',async()=>{
 page('?neon_auth_session_verifier=one-use');await import('../src/auth/page-client.js');
 expect(auth.resume).toHaveBeenCalledOnce();expect(auth.continueLogin).not.toHaveBeenCalled();
});
it('continues the native provider flow only when its signed-query manifest exists',async()=>{
 page('?client_id=desktop&ba_param=client_id&sig=synthetic');await import('../src/auth/page-client.js');
 expect(auth.continueLogin).toHaveBeenCalledOnce();expect(auth.resume).not.toHaveBeenCalled();
});
