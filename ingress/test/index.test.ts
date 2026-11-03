import { expect, it, vi } from 'vitest';
import { proxyRequest } from '../src/index.js';

const origin='https://native.compute.example.invalid';
function setup() {
  const limit=()=>({limit:vi.fn(async()=>({success:true}))});
  const env={ORIGIN_URL:origin,KENKUI_ORIGIN_SECRET:'server-only',ALLOWED_ORIGINS:'https://studio.invalid',PUBLIC_LIMIT:limit(),LOGIN_LIMIT:limit(),COSTLY_LIMIT:limit()};
  const request=(path:string,init:RequestInit={})=>new Request('https://api.invalid'+path,{...init,headers:{'CF-Connecting-IP':'192.0.2.1',...init.headers}});
  return {env,request};
}
it('replaces the secret, retains exact path/query/raw body and never follows a redirect with credentials',async()=>{
  const {env,request}=setup(),bytes=new Uint8Array([0,255,13,10,32,123,125]);
  const send=vi.fn(async(req:Request)=>{
    expect(req.url).toBe(origin+'/v1/billing/webhooks/stripe?literal=%2F');
    expect(req.redirect).toBe('manual');expect(req.headers.get('x-kenkui-origin-secret')).toBe('server-only');
    expect(req.headers.get('authorization')).toBe('Bearer private');
    expect(new Uint8Array(await req.arrayBuffer())).toEqual(bytes);
    return new Response(null,{status:303,headers:{location:'https://external.invalid/signed'}});
  });
  const res=await proxyRequest(request('/v1/billing/webhooks/stripe?literal=%2F',{method:'POST',body:bytes,headers:{authorization:'Bearer private',cookie:'private=cookie','X-Kenkui-Origin-Secret':'caller'}}),env,send);
  expect(res.status).toBe(303);expect(res.headers.get('location')).toBe('https://external.invalid/signed');expect(send).toHaveBeenCalledTimes(1);
});
it('passes errors, CORS and multiple cookies without reconstructing the upstream response',async()=>{
  const {env,request}=setup(),headers=new Headers({'access-control-allow-origin':'https://studio.invalid','content-type':'application/json','x-request-id':'upstream'});
  headers.append('set-cookie','a=1; Secure; HttpOnly');headers.append('set-cookie','b=2; Secure; HttpOnly');
  const upstream=new Response('{"error":"unauthorized"}',{status:401,headers});
  const res=await proxyRequest(request('/v1/jobs',{headers:{origin:'https://studio.invalid'}}),env,async()=>upstream);
  expect(res).toBe(upstream);expect(res.headers.getSetCookie()).toHaveLength(2);expect(res.status).toBe(401);expect(await res.text()).toContain('unauthorized');
});
it.each(['text/event-stream','audio/mp4'])('streams %s without buffering and propagates consumer cancellation',async(type)=>{
  const {env,request}=setup(),cancel=vi.fn(),abort=new AbortController();let upstreamSignal:AbortSignal|undefined;
  const body=new ReadableStream<Uint8Array>({start(c){c.enqueue(new TextEncoder().encode('id: 9007199254740993\n\n'));},cancel},{highWaterMark:0});
  const res=await proxyRequest(request('/v1/jobs/id/events',{signal:abort.signal,headers:{'Last-Event-ID':'9007199254740992'}}),env,async req=>{
    upstreamSignal=req.signal;expect(req.headers.get('Last-Event-ID')).toBe('9007199254740992');return new Response(body,{headers:{'content-type':type}});
  });
  const reader=res.body!.getReader();expect(new TextDecoder().decode((await reader.read()).value)).toContain('9007199254740993');
  await reader.cancel();expect(cancel).toHaveBeenCalledTimes(1);abort.abort();expect(upstreamSignal!.aborted).toBe(true);
});
it('bounds route classes using only Cloudflare IP and preserves preflight CORS on local errors',async()=>{
  const {env,request}=setup();env.LOGIN_LIMIT.limit.mockResolvedValue({success:false});const send=vi.fn();
  const res=await proxyRequest(request('/v1/auth/login?subject=changed',{headers:{origin:'https://studio.invalid',authorization:'Bearer forged', 'X-Forwarded-For':'spoof'}}),env,send);
  expect(res.status).toBe(429);expect(res.headers.get('retry-after')).toBe('60');expect(res.headers.get('access-control-allow-origin')).toBe('https://studio.invalid');expect(send).not.toHaveBeenCalled();
  expect(env.LOGIN_LIMIT.limit).toHaveBeenCalledExactlyOnceWith({key:'login:192.0.2.1'});
  await proxyRequest(request('/v1/assets',{method:'POST'}),env,async()=>new Response());expect(env.COSTLY_LIMIT.limit).toHaveBeenCalledExactlyOnceWith({key:'costly:192.0.2.1'});
  await proxyRequest(request('/v1/health'),env,async()=>new Response());expect(env.PUBLIC_LIMIT.limit).toHaveBeenCalledExactlyOnceWith({key:'public:192.0.2.1'});
  const preflight=await proxyRequest(request('/v1/jobs',{method:'OPTIONS',headers:{origin:'https://studio.invalid','Access-Control-Request-Method':'POST'}}),env,async req=>new Response(req.method));expect(await preflight.text()).toBe('OPTIONS');
});
it('fails closed on missing IP, binding, secret or invalid origin and sanitizes upstream failures',async()=>{
  const {env,request}=setup(),send=vi.fn(async()=>new Response());
  const noIp=request('/v1/health');noIp.headers.delete('cf-connecting-ip');expect((await proxyRequest(noIp,env,send)).status).toBe(503);
  for(const changed of [{...env,KENKUI_ORIGIN_SECRET:''},{...env,ORIGIN_URL:'https://bad.invalid/path'},{...env,LOGIN_LIMIT:undefined}]) expect((await proxyRequest(request('/v1/health'),changed as typeof env,send)).status).toBe(503);
  expect(send).not.toHaveBeenCalled();
  const failed=await proxyRequest(request('/v1/health'),env,async()=>{throw Error('private-secret?query');});expect(failed.status).toBe(502);expect(await failed.text()).not.toContain('private-secret');
});
it.each([
  ['POST','/v1/jobs','/v1/%6Aobs','COSTLY_LIMIT'],
  ['POST','/v1/jobs/preflight','/%76%31/jobs/%70reflight','COSTLY_LIMIT'],
  ['GET','/v1/auth/login','/v1/%61uth/%6cogin','LOGIN_LIMIT'],
] as const)('charges equivalent %s %s spellings to the same scoped limit without rewriting wire paths',async(method,plain,encoded,binding)=>{
  const {env,request}=setup(),received:string[]=[];
  const send=async(req:Request)=>{received.push(req.url);return new Response('matched');};
  for(const path of [plain,encoded])expect((await proxyRequest(request(path,{method}),env,send)).status).toBe(200);
  expect(env[binding].limit.mock.calls).toEqual([[{key:(binding==='LOGIN_LIMIT'?'login':'costly')+':192.0.2.1'}],[{key:(binding==='LOGIN_LIMIT'?'login':'costly')+':192.0.2.1'}]]);
  expect(received).toEqual([origin+plain,origin+encoded]);
});
it.each(['/v1/%','/v1/%C0/jobs','/v1/%2Fjobs','/v1/%256Aobs'])('rejects ambiguous encoded routing %s before origin fetch',async(path)=>{
  const {env,request}=setup(),send=vi.fn(async()=>new Response());
  expect((await proxyRequest(request(path),env,send)).status).toBe(400);expect(send).not.toHaveBeenCalled();
});
