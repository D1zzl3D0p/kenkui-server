import type { Hono } from 'hono';
import { ApiError,type ApiEnv } from '../types.js';
import { readBoundedBody } from '../routes/assets.js';
import { unavailable } from '../db/migration.js';
import type { Admission } from './admission.js';
import type { MigrationChallenges } from './migration-challenge.js';
import type { NeonAuth,NeonAuthConfig } from './neon.js';
import type { LegacyRecovery } from './recovery.js';
import { AUTH_PROTOCOL_VERSION,NATIVE_REDIRECT_URI } from './native.js';

declare const AUTH_BROWSER_SOURCE:string;
const escape=(s:string)=>s.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');

interface MigrationOptions {
  auth:NeonAuth;
  admission:Pick<Admission,'onboard'>;
  challenges:Pick<MigrationChallenges,'issue'|'verify'>;
}

async function migrationInput(request:Request,fields:string[]):Promise<Record<string,string>> {
  if(request.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase()!=='application/json')
    throw new ApiError(415,'validation_error','Expected JSON.');
  const raw=await readBoundedBody(request,2048,'Request is too large.');
  try {
    const body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
    if(!body||Array.isArray(body)||typeof body!=='object'||Object.keys(body).length!==fields.length||
        !fields.every(key=>Object.hasOwn(body,key)&&typeof body[key]==='string')) throw Error();
    return body;
  } catch {throw new ApiError(422,'validation_error','Request validation failed');}
}

export function registerMigrationRoutes(app:Hono<ApiEnv>,migration?:MigrationOptions) {
  for(const action of ['onboarding','migration/challenge','migration/verify'] as const) {
    app.post('/v1/auth/'+action,async c=>{
      if(!migration) throw unavailable();
      const who=await migration.auth.authenticateRequest(c.req.raw);
      if(!who) throw new ApiError(401,'unauthenticated','Sign in to continue.');
      if(action==='onboarding') {
        await migrationInput(c.req.raw,[]);
        return c.json(await migration.admission.onboard(who));
      }
      if(action==='migration/challenge') {
        const body=await migrationInput(c.req.raw,['legacyEmail']);
        return c.json(await migration.challenges.issue(who,body.legacyEmail!),202);
      }
      const body=await migrationInput(c.req.raw,['challengeId','code']);
      return c.json(await migration.challenges.verify(who,body.challengeId!,body.code!));
    });
  }
}

export function registerAuthRoutes(app:Hono<ApiEnv>,config:NeonAuthConfig,options:{migration?:MigrationOptions;recovery?:LegacyRecovery;browserSource?:string}={}) {
  app.use('/v1/auth/*',async(c,next)=>{
    // Set before downstream errors too: token/recovery responses must never be cached.
    c.header('Cache-Control','no-store');c.header('Pragma','no-cache');c.header('Referrer-Policy','no-referrer');
    await next();
  });
  registerMigrationRoutes(app,options.migration);
  const page=(kind:'login'|'consent')=>{
    const content=kind==='login'?'<button data-provider="google">Continue with Google</button> <button data-provider="github">Continue with GitHub</button>':
      '<p id="client"></p><p id="scope"></p><p id="resource"></p><p id="callback"></p><p>This desktop app receives the sign-in code on your own computer. Approve only the app you opened.</p><button id="accept" disabled>Allow</button> <button id="deny" disabled>Deny</button>';
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kenkui sign in</title></head><body data-page="${kind}" data-issuer="${escape(config.issuer)}" data-client-id="${escape(config.nativeClientId)}" data-resource="${escape(config.resource)}"><main><h1>${kind==='login'?'Sign in to Kenkui':'Allow Kenkui Desktop access?'}</h1>${content}<p id="status" role="status"></p><button id="signout">Sign out of this browser</button></main><script src="/v1/auth/client.js" defer></script></body></html>`;
  };
  for(const kind of ['login','consent'] as const) app.get('/v1/auth/'+kind,c=>{
    c.header('Content-Security-Policy',`default-src 'none'; script-src 'self'; connect-src ${new URL(config.issuer).origin}; frame-src ${new URL(config.issuer).origin}; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`);
    c.header('X-Content-Type-Options','nosniff');return c.html(page(kind));
  });
  app.get('/v1/auth/client.js',c=>{
    const source=options.browserSource??(typeof AUTH_BROWSER_SOURCE==='string'?AUTH_BROWSER_SOURCE:undefined);
    if(!source) throw new ApiError(503,'auth_unavailable','Authentication asset is unavailable.');
    return c.body(source,200,{'Content-Type':'application/javascript; charset=utf-8','X-Content-Type-Options':'nosniff'});
  });
  app.get('/v1/auth/callback',()=>{throw new ApiError(401,'login_required','Sign in again using Google or GitHub.');});
  app.get('/v1/auth/session',c=>c.json({userId:c.get('principal').userId}));
  // SDK signOut owns the managed cookie. This route cannot revoke already-issued JWTs.
  app.post('/v1/auth/logout',c=>c.json({protocolVersion:AUTH_PROTOCOL_VERSION,managedSignOutRequired:true}));
  app.get('/v1/auth/native/config',c=>c.json({protocolVersion:AUTH_PROTOCOL_VERSION,redirectUri:NATIVE_REDIRECT_URI,issuer:config.issuer,jwksUrl:config.jwksUrl,clientId:config.nativeClientId,resource:config.resource,discoveryUrl:config.issuer+'/.well-known/oauth-authorization-server',scopes:'openid profile email offline_access'}));
  for(const path of ['authorize','exchange','refresh','logout']) app.all('/v1/auth/native/'+path,()=>{throw new ApiError(426,'client_upgrade_required','Update Kenkui Desktop and sign in again.',{protocolVersion:AUTH_PROTOCOL_VERSION});});
  app.post('/v1/auth/migration/link',async c=>{
    if(!options.recovery) {
      throw new ApiError(409,'recovery_required','Your existing account needs migration verification.');
    }
    if(Number(c.req.header('Content-Length')??0)>18_000) {
      throw new ApiError(413,'request_too_large','Request is too large.');
    }
    const reader=c.req.raw.body?.getReader();
    const chunks:Uint8Array[]=[];
    let bytes=0;
    if(reader) {
      try {
        for(;;) {
          const part=await reader.read();
          if(part.done) break;
          bytes+=part.value.byteLength;
          if(bytes>18_000) {
            await reader.cancel();
            throw new ApiError(413,'request_too_large','Request is too large.');
          }
          chunks.push(part.value);
        }
      } finally {
        reader.releaseLock();
      }
    }
    const joined=new Uint8Array(bytes);
    let offset=0;
    for(const chunk of chunks) {
      joined.set(chunk,offset);
      offset+=chunk.byteLength;
    }
    const body=new TextDecoder().decode(joined);
    let input:unknown;
    try {
      input=JSON.parse(body);
    } catch {
      throw new ApiError(422,'validation_error','Request validation failed');
    }
    if(
      !input || typeof input!=='object' ||
      Object.keys(input).length!==1 ||
      !('legacyAccessToken' in input) || typeof input.legacyAccessToken!=='string'
    ) {
      throw new ApiError(422,'validation_error','Request validation failed');
    }
    return c.json(await options.recovery.link(c.req.raw,input.legacyAccessToken));
  });
}
