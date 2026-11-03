/// <reference path="../worker-configuration.d.ts" />

type Config = Pick<Cloudflare.Env,'ORIGIN_URL'|'ALLOWED_ORIGINS'|'KENKUI_ORIGIN_SECRET'|'PUBLIC_LIMIT'|'LOGIN_LIMIT'|'COSTLY_LIMIT'>;
type Bindings = {[K in keyof Config]:Config[K] extends string?string:Config[K]};
type Send = (request:Request)=>Promise<Response>;

// Hono decodes unreserved escapes before routing. Accept those aliases without
// rewriting wire bytes; reject other/malformed escapes instead of routing ambiguously.
function routingPath(path:string):string {
  if(/%(?![0-9a-fA-F]{2})/.test(path))throw Error('Invalid path');
  return path.replace(/%([0-9a-fA-F]{2})/g,(_escape,hex:string)=>{
    const character=String.fromCharCode(Number.parseInt(hex,16));
    if(!/^[A-Za-z0-9._~-]$/.test(character))throw Error('Ambiguous path');
    return character;
  });
}
function scope(request:Request,path:string):'login'|'costly'|'public' {
  if(path.startsWith('/v1/auth/'))return 'login';
  if((request.method==='POST' && (path==='/v1/jobs'||path==='/v1/jobs/preflight'||path==='/v1/assets'||/^\/v1\/assets\/[^/]+\/cover$/.test(path)||path==='/v1/billing/checkout')) ||
    (request.method==='GET' && /^\/v1\/assets\/[^/]+\/(book|cover)$/.test(path)))return 'costly';
  return 'public';
}
function failure(request:Request,env:Bindings,status:number,code:string,message:string):Response {
  const requestId=crypto.randomUUID(),origin=request.headers.get('origin'),headers=new Headers({'X-Request-ID':requestId,'Cache-Control':'no-store','Vary':'Origin'});
  if(origin && env.ALLOWED_ORIGINS?.split(',').includes(origin)) {
    headers.set('Access-Control-Allow-Origin',origin);headers.set('Access-Control-Allow-Credentials','true');
  }
  if(status===429)headers.set('Retry-After','60');
  return Response.json({error:{code,message,details:{},requestId}},{status,headers});
}

/** Transparent, bounded-memory ingress. No body reads, retries, redirect follows or identity claims. */
export async function proxyRequest(request:Request,env:Bindings,send:Send=fetch):Promise<Response> {
  let origin:URL;
  try {
    origin=new URL(env.ORIGIN_URL);
    if(origin.protocol!=='https:'||origin.username||origin.password||origin.pathname!=='/'||origin.search||origin.hash||
      !env.KENKUI_ORIGIN_SECRET||!env.ALLOWED_ORIGINS||![env.PUBLIC_LIMIT,env.LOGIN_LIMIT,env.COSTLY_LIMIT].every(x=>typeof x?.limit==='function'))throw Error();
  } catch {return failure(request,env,503,'configuration_error','Ingress configuration is unavailable.');}
  // Cloudflare overwrites this header for external requests. Never use forwarded-for,
  // JWT claims, cookies or caller IDs as limiter keys. Missing edge metadata fails closed.
  const ip=request.headers.get('CF-Connecting-IP');
  if(!ip || !/^[a-fA-F0-9:.]+$/.test(ip))return failure(request,env,503,'configuration_error','Ingress client metadata is unavailable.');
  const url=new URL(request.url);
  let path:string;
  try{path=routingPath(url.pathname);}catch{return failure(request,env,400,'invalid_path','Request path is not allowed.');}
  const category=scope(request,path);
  try {
    const limiter=category==='login'?env.LOGIN_LIMIT:category==='costly'?env.COSTLY_LIMIT:env.PUBLIC_LIMIT;
    if(!(await limiter.limit({key:category+':'+ip})).success)return failure(request,env,429,'http_error','Too many requests');
    const target=new URL(origin.origin);target.pathname=url.pathname;target.search=url.search;
    const headers=new Headers(request.headers);
    headers.set('X-Kenkui-Origin-Secret',env.KENKUI_ORIGIN_SECRET);
    for(const name of ['host','forwarded','x-forwarded-host','x-forwarded-proto','x-forwarded-for'])headers.delete(name);
    // Returning the upstream Response preserves Set-Cookie, Location, errors, CORS,
    // SSE timing, range/download bytes and the response body's cancellation chain.
    return await send(new Request(target,{method:request.method,headers,body:request.body,redirect:'manual',signal:request.signal,...(request.body?{duplex:'half'}:{})}));
  } catch {
    console.error(JSON.stringify({event:'ingress.upstream_failed',scope:category}));
    return failure(request,env,502,'upstream_unavailable','API is temporarily unavailable.');
  }
}
export default {fetch(request,env){return proxyRequest(request,env);}} satisfies ExportedHandler<Cloudflare.Env>;
