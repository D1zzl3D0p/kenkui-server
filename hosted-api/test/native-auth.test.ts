import { describe, expect, it, vi } from 'vitest';
import { NativeAuthClient, NATIVE_REDIRECT_URI, type NativeCredentials } from '../src/auth/native.js';

const issuer='https://branch.example.invalid/neondb/auth';
const config={issuer,clientId:'desktop',resource:'https://api.example.invalid'};
function setup() {
 let saved:NativeCredentials|null=null; let queue:Promise<unknown>=Promise.resolve();
 const storage={load:vi.fn(async()=>saved),save:vi.fn(async(v:NativeCredentials)=>{saved=v;}),clear:vi.fn(async()=>{saved=null;}),exclusive:<T>(fn:()=>Promise<T>):Promise<T>=>{const next=queue.then(fn,fn);queue=next.catch(()=>{});return next;}};
 const fetcher=vi.fn(async(url:string|URL|Request,init?:RequestInit)=>{
   if(String(url).includes('.well-known')) return Response.json({issuer,authorization_endpoint:issuer+'/oauth2/authorize',token_endpoint:issuer+'/oauth2/token',revocation_endpoint:issuer+'/oauth2/revoke'});
   if(String(url).endsWith('/revoke')) return new Response(null,{status:200});
   return Response.json({access_token:'signed.jwt.token',refresh_token:'rotated-refresh',token_type:'Bearer',expires_in:3600});
 });
 const verify=vi.fn(async()=>({subject:'neon-user',expiresAt:Math.floor(Date.now()/1000)+3600}));
 const client=new NativeAuthClient(config,storage,verify,fetcher);
 return {client,storage,fetcher,verify};
}
async function callback(client:NativeAuthClient) {const url=new URL(await client.start());return `${NATIVE_REDIRECT_URI}?code=code&state=${url.searchParams.get('state')}&iss=${encodeURIComponent(issuer)}`;}
function deferred<T>() {
 let resolve!:(value:T)=>void;
 const promise=new Promise<T>(done=>{resolve=done;});
 return {promise,resolve};
}
const metadataResponse=()=>Response.json({issuer,authorization_endpoint:issuer+'/oauth2/authorize',token_endpoint:issuer+'/oauth2/token',revocation_endpoint:issuer+'/oauth2/revoke'});
const callbackFor=(authorizationUrl:string)=>`${NATIVE_REDIRECT_URI}?code=code&state=${new URL(authorizationUrl).searchParams.get('state')}&iss=${encodeURIComponent(issuer)}`;

describe('native protocol v2',()=>{
 it('logout cancels a start waiting for discovery before it can install an attempt',async()=>{
   const s=setup();
   const discovery=deferred<Response>();
   s.fetcher.mockImplementationOnce(()=>discovery.promise);
   const outcome=s.client.start().then(url=>({url}),error=>({error}));
   expect(s.fetcher).toHaveBeenCalledOnce();
   expect(await s.client.logout()).toEqual({revoked:true});
   discovery.resolve(metadataResponse());
   const result=await outcome;
   // Reproduce the harmful callback too if the obsolete start erroneously succeeds.
   if('url' in result) await s.client.finish(callbackFor(result.url));
   expect(result).toMatchObject({error:expect.objectContaining({message:'Login attempt cancelled'})});
   expect(await s.storage.load()).toBeNull();
   expect(s.storage.save).not.toHaveBeenCalled();
 });
 it('logout cancels a start waiting for PKCE hashing',async()=>{
   const s=setup();
   const entered=deferred<void>();
   const digest=deferred<ArrayBuffer>();
   const spy=vi.spyOn(crypto.subtle,'digest').mockImplementationOnce(()=>{entered.resolve();return digest.promise;});
   try {
     const outcome=s.client.start().then(url=>({url}),error=>({error}));
     await entered.promise;
     await s.client.logout();
     digest.resolve(new ArrayBuffer(32));
     expect(await outcome).toMatchObject({error:expect.objectContaining({message:'Login attempt cancelled'})});
     expect(await s.storage.load()).toBeNull();
     expect(s.storage.save).not.toHaveBeenCalled();
   } finally{spy.mockRestore();}
 });
 it('overlapping starts sharing discovery keep only the newest attempt',async()=>{
   const s=setup();
   const discovery=deferred<Response>();
   s.fetcher.mockImplementationOnce(()=>discovery.promise);
   const older=s.client.start().then(url=>({url}),error=>({error}));
   const newer=s.client.start();
   discovery.resolve(metadataResponse());
   expect(await older).toMatchObject({error:expect.objectContaining({message:'Login attempt cancelled'})});
   await s.client.finish(callbackFor(await newer));
   expect(await s.storage.load()).toMatchObject({accessToken:'signed.jwt.token',subject:'neon-user'});
 });
 it('an older pending PKCE hash cannot overwrite a newer completed start',async()=>{
   const s=setup();
   const entered=deferred<void>();
   const digest=deferred<ArrayBuffer>();
   const spy=vi.spyOn(crypto.subtle,'digest').mockImplementationOnce(()=>{entered.resolve();return digest.promise;});
   try {
     const older=s.client.start().then(url=>({url}),error=>({error}));
     await entered.promise;
     const newer=await s.client.start();
     digest.resolve(new ArrayBuffer(32));
     expect(await older).toMatchObject({error:expect.objectContaining({message:'Login attempt cancelled'})});
     await s.client.finish(callbackFor(newer));
     expect(await s.storage.load()).toMatchObject({accessToken:'signed.jwt.token',subject:'neon-user'});
   } finally{spy.mockRestore();}
 });
 it('never sends refresh if uncertainty persistence fails, and never replays after an ambiguous POST',async()=>{
   const s=setup();const old={accessToken:'old.jwt.token',refreshToken:'old-refresh',expiresAt:1,subject:'neon-user'};await s.storage.save(old);
   s.storage.save.mockRejectedValueOnce(new Error('keychain locked'));
   await expect(s.client.getAccessToken()).rejects.toThrow('keychain');expect(s.fetcher.mock.calls.filter(c=>String(c[0]).endsWith('/token'))).toHaveLength(0);expect(await s.storage.load()).toEqual(old);
   s.fetcher.mockRejectedValueOnce(new TypeError('response lost'));
   await expect(s.client.getAccessToken()).rejects.toThrow('response lost');expect(await s.storage.load()).toEqual({...old,refreshUncertain:true});
   const count=s.fetcher.mock.calls.length;await expect(s.client.getAccessToken()).rejects.toThrow('unknown');expect(s.fetcher).toHaveBeenCalledTimes(count);
 });
 it('serializes logout after refresh so stale work cannot restore credentials',async()=>{
   const s=setup();await s.storage.save({accessToken:'old.jwt.token',refreshToken:'old',expiresAt:1,subject:'neon-user'});
   const [token,result]=await Promise.all([s.client.getAccessToken(),s.client.logout()]);expect(token).toBe('signed.jwt.token');expect(result).toEqual({revoked:true});expect(await s.storage.load()).toBeNull();
   const form=new URLSearchParams(String(s.fetcher.mock.calls.at(-1)![1]!.body));expect(form.get('token')).toBe('rotated-refresh');
 });
 it('uses fresh S256 PKCE, offline_access, fixed loopback, resource on authorize AND token, no secret',async()=>{
   const {client,fetcher,storage}=setup(); const url=new URL(await client.start());
   expect(url.searchParams.get('redirect_uri')).toBe(NATIVE_REDIRECT_URI); expect(url.searchParams.get('resource')).toBe(config.resource); expect(url.searchParams.get('scope')).toContain('offline_access'); expect(url.searchParams.get('code_challenge_method')).toBe('S256');
   await client.finish(`${NATIVE_REDIRECT_URI}?code=code&state=${url.searchParams.get('state')}&iss=${encodeURIComponent(issuer)}`);
   const init=fetcher.mock.calls.at(-1)![1]!; const form=new URLSearchParams(String(init.body));
   expect(form.get('resource')).toBe(config.resource); expect(form.get('redirect_uri')).toBe(NATIVE_REDIRECT_URI); expect(form.get('client_id')).toBe('desktop'); expect(form.has('client_secret')).toBe(false); expect(form.get('code_verifier')!.length).toBeGreaterThanOrEqual(43);
   const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(form.get('code_verifier')!));
   expect(Buffer.from(digest).toString('base64url')).toBe(url.searchParams.get('code_challenge')); expect(storage.save).toHaveBeenCalledOnce();
 });
 it.each(['state=wrong','iss=https%3A%2F%2Fevil.invalid','error=access_denied'])('rejects callback %s and consumes attempt',async change=>{
   const {client,storage,fetcher}=setup(); const url=new URL(await callback(client)); const [key,value]=change.split('='); url.searchParams.set(key!,decodeURIComponent(value!));
   await expect(client.finish(url.href)).rejects.toThrow(); await expect(client.finish(url.href)).rejects.toThrow(); expect(storage.save).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledOnce();
 });
 it('rejects different port/path, duplicate state, missing issuer and expired attempts',async()=>{
   for(const change of [(u:URL)=>u.port='43828',(u:URL)=>u.pathname='/other',(u:URL)=>u.searchParams.append('state','extra'),(u:URL)=>u.searchParams.delete('iss')]) {const {client,storage}=setup();const url=new URL(await callback(client));change(url);await expect(client.finish(url.href)).rejects.toThrow();expect(storage.save).not.toHaveBeenCalled();}
   const {client}=setup(); const url=await callback(client); const now=Date.now(); const clock=vi.spyOn(Date,'now').mockReturnValue(now+600_000); await expect(client.finish(url)).rejects.toThrow();clock.mockRestore();
 });
 it('rejects wrong discovery issuer and opaque/unverified access tokens',async()=>{
   const {client,fetcher}=setup();fetcher.mockResolvedValueOnce(Response.json({issuer:'https://evil.invalid'})); await expect(client.start()).rejects.toThrow();
   const s=setup();const url=await callback(s.client);s.verify.mockRejectedValueOnce(new Error('signature'));await expect(s.client.finish(url)).rejects.toThrow();expect(s.storage.save).not.toHaveBeenCalled();
 });
 it('serializes on-demand refresh, stores rotated token atomically, and performs no idle polling',async()=>{
   const {client,storage,fetcher}=setup();await storage.save({accessToken:'old.jwt.token',refreshToken:'old-refresh',expiresAt:1,subject:'neon-user'});
   expect(await Promise.all([client.getAccessToken(),client.getAccessToken()])).toEqual(['signed.jwt.token','signed.jwt.token']);
   expect(fetcher.mock.calls.filter(c=>String(c[0]).endsWith('/token'))).toHaveLength(1);
   expect((await storage.load())?.refreshToken).toBe('rotated-refresh');
 });
 it('retains credentials on transport failure and rejects refresh subject switching',async()=>{
   const s=setup();const old={accessToken:'old.jwt.token',refreshToken:'old-refresh',expiresAt:1,subject:'owner'};await s.storage.save(old);s.fetcher.mockRejectedValueOnce(new TypeError('offline'));await expect(s.client.getAccessToken()).rejects.toThrow();expect(await s.storage.load()).toEqual(old);
   await expect(s.client.getAccessToken()).rejects.toThrow();expect(await s.storage.load()).toEqual({...old,refreshUncertain:true});
   const calls=s.fetcher.mock.calls.length;await expect(s.client.getAccessToken()).rejects.toThrow('unknown');expect(s.fetcher).toHaveBeenCalledTimes(calls);
 });
 it('revokes refresh and clears local credentials even if revocation fails',async()=>{
   const s=setup(); await s.client.finish(await callback(s.client)); s.fetcher.mockRejectedValueOnce(new TypeError('offline'));
   expect(await s.client.logout()).toEqual({revoked:false});expect(await s.storage.load()).toBeNull();
 });
});
