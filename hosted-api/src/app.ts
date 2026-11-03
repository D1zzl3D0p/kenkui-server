import { randomUUID, timingSafeEqual, createHash } from 'node:crypto';
import { Hono, type Context } from 'hono';
import { ApiError, type ApiDeps, type ApiEnv } from './types.js';
import { registerCatalog } from './routes/catalog.js';

const methods = ['GET','POST','DELETE','OPTIONS'];
const corsHeaders = ['Accept','Accept-Language','Authorization','Content-Language','Content-Type','Idempotency-Key'];

function protectedPath(path: string): boolean {
  return path.startsWith('/v1/jobs') || path.startsWith('/v1/assets') ||
    (path.startsWith('/v1/billing') && path !== '/v1/billing/webhooks/stripe') ||
    (path.startsWith('/v1/notifications') && path !== '/v1/notifications/unsubscribe') ||
    ['/v1/auth/session','/v1/auth/logout'].includes(path);
}

function credentialPresent(request: Request): boolean {
  const authorization = request.headers.get('authorization') ?? '';
  if (/^bearer /i.test(authorization)) return Boolean(authorization.slice(7));
  return /(?:^|;\s*)__Host-kenkui-session=[^;]+/.test(request.headers.get('cookie') ?? '');
}

function equalSecret(actual: string, expected: string): boolean {
  const digest = (value:string) => createHash('sha256').update(value).digest();
  return timingSafeEqual(digest(actual),digest(expected));
}

export function createApi(deps: ApiDeps): Hono<ApiEnv> {
  const app = new Hono<ApiEnv>();
  const newId = deps.requestId ?? randomUUID;
  const id = (c:Context<ApiEnv>) => {
    if (!c.get('requestId')) c.set('requestId',newId());
    return c.get('requestId');
  };
  app.onError((error,c) => {
    const known = error instanceof ApiError;
    const safe = known ? error : new ApiError(500,'internal_server_error','Internal server error');
    const requestId = id(c);
    if (!known) deps.log?.({event:'request.failed',method:c.req.method,path:c.req.path,requestId,statusCode:500});
    return c.json({error:{code:safe.code,message:safe.message,details:safe.details,requestId}},safe.status, {...safe.headers,'X-Request-ID':requestId});
  });

  // The native Function URL remains public. Gate every invocation before parsing/auth/DB.
  app.use('*',async (c,next) => {
    const expected = deps.config.ingressSecret;
    if (expected && !equalSecret(c.req.header('X-Kenkui-Origin-Secret') ?? '',expected)) {
      throw new ApiError(403,'invalid_origin','Request origin is not allowed.');
    }
    await next();
  });

  app.use('*',async (c,next) => {
    const origin = c.req.header('Origin');
    if (!origin || !deps.config.allowedOrigins.length) {await next(); return;}
    const allowed = deps.config.allowedOrigins.includes(origin);
    if (c.req.method === 'OPTIONS' && c.req.header('Access-Control-Request-Method')) {
      const failures:string[] = [];
      if (!allowed) failures.push('origin');
      if (!methods.includes(c.req.header('Access-Control-Request-Method')!)) failures.push('method');
      if ((c.req.header('Access-Control-Request-Headers') ?? '').split(',').filter(Boolean).some(h => !corsHeaders.some(a => a.toLowerCase() === h.trim().toLowerCase()))) failures.push('headers');
      return c.text(failures.length ? `Disallowed CORS ${failures.join(', ')}` : 'OK',failures.length ? 400 : 200,{
        'Content-Type':'text/plain; charset=utf-8',
        'Access-Control-Allow-Credentials':'true',
        'Access-Control-Allow-Methods':methods.join(', '),
        'Access-Control-Allow-Headers':corsHeaders.join(', '),
        'Access-Control-Max-Age':'600',
        'Vary':'Origin',
        ...(allowed ? {'Access-Control-Allow-Origin':origin} : {}),
      });
    }
    await next();
    c.header('Access-Control-Allow-Credentials','true');
    if (allowed) {c.header('Access-Control-Allow-Origin',origin); c.header('Vary',c.res.headers.get('Vary') ? `${c.res.headers.get('Vary')}, Origin` : 'Origin');}
  });

  // Auth responses (including early failures) carry credentials or recovery state.
  app.use('/v1/auth/*',async(c,next)=>{
    c.header('Cache-Control','no-store');c.header('Pragma','no-cache');
    await next();
  });

  // Match the frozen early-auth request-ID behavior: ignore caller ID on rejection.
  app.use('*',async (c,next) => {
    if (protectedPath(c.req.path)) {
      if (!credentialPresent(c.req.raw)) throw new ApiError(401,'unauthenticated','Sign in to continue.');
      if (!deps.auth) throw new ApiError(503,'auth_unavailable','Authentication is not configured.');
      const principal = await deps.auth.verify(c.req.raw);
      if (!principal) throw new ApiError(401,'unauthenticated','Sign in with an invited account to continue.');
      c.set('principal',principal);
    }
    await next();
  });

  app.use('*',async (c,next) => {
    c.set('requestId',c.req.header('X-Request-ID') || newId());
    await next();
    c.header('X-Request-ID',id(c));
    if (c.req.path.startsWith('/v1/auth/native/')) {c.header('Cache-Control','no-store');c.header('Pragma','no-cache');}
    deps.log?.({event:'request.completed',method:c.req.method,path:c.req.path,requestId:id(c),statusCode:c.res.status});
  });

  app.use('*',async (c,next) => {
    if (c.req.method === 'HEAD' && ['/v1/health','/v1/capabilities','/v1/voices'].includes(c.req.path)) {
      throw new ApiError(405,'method_not_allowed','Method not allowed');
    }
    await next();
  });
  registerCatalog(app,deps.config.catalog);
  app.notFound(() => {throw new ApiError(404,'not_found','Not found');});
  return app;
}
