import { describe,expect,it,vi } from 'vitest';
import { createBrowserAuth } from '../src/auth/browser.js';

function setup() {
 let visible=true;
 const sdk={getSession:vi.fn(async()=>({data:{session:{id:'session'}}})),token:vi.fn(async()=>({data:{token:'jwt'}})),signIn:{social:vi.fn(async()=>({data:{}}))},signOut:vi.fn(async()=>({data:{}})),oauth2:{continue:vi.fn(async()=>({data:{}})),consent:vi.fn(async()=>({data:{}})),publicClient:vi.fn(async()=>({data:{client_name:'Kenkui',client_id:'desktop'}}))}};
 return {sdk,auth:createBrowserAuth('https://auth.invalid/db/auth',()=>visible,sdk),hide:()=>{visible=false;}};
}
it('uses the actual pinned SDK with adapter options, cookie credentials and OAuth methods',async()=>{
  const calls:{url:string;init:RequestInit|undefined}[]=[];
  const fetcher=vi.fn(async(input:string|URL|Request,init?:RequestInit)=>{
    const url=input instanceof Request?input.url:String(input);
    calls.push({url,init});
    if(url.includes('/get-session')) return Response.json({session:{id:'test'},user:{id:'user'}},{headers:{'set-auth-jwt':'signed.jwt.token'}});
    if(url.includes('/token')) return Response.json({token:'signed.jwt.token'});
    return Response.json({redirect:false,client_id:'desktop',client_name:'Kenkui'});
  });
  vi.stubGlobal('fetch',fetcher);
  const signedQuery='client_id=desktop&scope=openid+email&state=opaque%2Bstate&ba_param=client_id&ba_param=scope&ba_param=state&ba_param=ba_param&sig=signature';
  vi.stubGlobal('window',{location:{href:'https://app.invalid/login?'+signedQuery,search:'?'+signedQuery},addEventListener:vi.fn(),removeEventListener:vi.fn()});
  try {
    const auth=createBrowserAuth('https://auth.invalid/db/auth',()=>true);
    await auth.login('google');await auth.login('github');
    const session=await auth.resume();expect(session).toMatchObject({data:{session:{id:'test'}}});expect(await auth.accessToken()).toBe('signed.jwt.token');
    await auth.continueLogin();await auth.clientDetails('desktop');await auth.consent(false);await auth.signOut();
    expect(calls.map(c=>new URL(c.url).pathname)).toEqual(expect.arrayContaining(['/db/auth/sign-in/social','/db/auth/get-session','/db/auth/oauth2/continue','/db/auth/oauth2/public-client','/db/auth/oauth2/consent','/db/auth/sign-out']));
    expect(calls.every(c=>c.init?.credentials==='include')).toBe(true);
    const social=calls.filter(c=>c.url.includes('/sign-in/social'));expect(social.map(c=>JSON.parse(String(c.init?.body)))).toEqual([{provider:'google',oauth_query:signedQuery},{provider:'github',oauth_query:signedQuery}]);
    const consent=calls.find(c=>c.url.includes('/oauth2/consent'))!;expect(JSON.parse(String(consent.init?.body))).toMatchObject({accept:false,oauth_query:signedQuery});
  } finally{vi.unstubAllGlobals();}
});

describe('Managed browser lifecycle without background subscriptions',()=>{
 it('does no work on construction or while hidden; reads lazily on visible work',async()=>{
   const s=setup(); expect(s.sdk.getSession).not.toHaveBeenCalled();await s.auth.resume(); expect(s.sdk.getSession).toHaveBeenCalledOnce();
   s.hide();expect(await s.auth.resume()).toBeNull();await expect(s.auth.accessToken()).rejects.toThrow();expect(s.sdk.getSession).toHaveBeenCalledOnce();expect(s.sdk.token).not.toHaveBeenCalled();
 });
 it('retains SDK OAuth query/navigation and only exposes Google/GitHub',async()=>{
   const {sdk,auth}=setup();await auth.login('google');await auth.login('github');await expect(auth.login('apple' as never)).rejects.toThrow();
   expect(sdk.signIn.social.mock.calls).toEqual([[{provider:'google'}],[{provider:'github'}]]);
   await auth.continueLogin();expect(sdk.oauth2.continue).toHaveBeenCalledWith({selected:true});await auth.consent(false);expect(sdk.oauth2.consent).toHaveBeenCalledWith({accept:false});
 });
 it('surfaces errors rather than accepting stale UI state; signs out at Managed Auth',async()=>{
   const {sdk,auth}=setup(); expect(await auth.accessToken()).toBe('jwt');await auth.signOut();expect(sdk.signOut).toHaveBeenCalledOnce();
   sdk.token.mockResolvedValueOnce({data:{token:''}});await expect(auth.accessToken()).rejects.toThrow();
 });
});
