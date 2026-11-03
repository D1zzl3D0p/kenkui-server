import { expect,it } from 'vitest';
import { releaseEnvironment } from '../release-environment.js';
it('uses only explicitly selected API authority and restricted connection; never injected owner credentials',()=>{
  const pin={apiOrigin:'https://api.staging.kenkui.fm',issuer:'https://auth.invalid/db/auth',jwksUrl:'https://auth.invalid/db/auth/jwks',clientId:'public',resource:'https://api.staging.kenkui.fm'};
  const source={KENKUI_ENV:'staging',KENKUI_RELEASE_AUTHORITY:JSON.stringify(pin),DATABASE_URL:'postgresql://owner@injected/db'};
  expect(()=>releaseEnvironment(source)).toThrow('KENKUI_API_DATABASE_URL');
  const required=['KENKUI_ALLOWED_ORIGINS','KENKUI_ORIGIN_SECRET','R2_ENDPOINT','R2_BUCKET','R2_KEY_SALT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','KENKUI_MODAL_BRIDGE_URL','KENKUI_MODAL_PROXY_KEY','KENKUI_MODAL_PROXY_SECRET','KENKUI_BRIDGE_SECRET','KENKUI_UNSUBSCRIBE_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET','KENKUI_WEB_ORIGIN','KENKUI_STRIPE_API_VERSION'];
  const env={...Object.fromEntries(required.map(name=>[name,'synthetic'])),...source,KENKUI_API_DATABASE_URL:'postgresql://api_runtime@restricted/db',UNRELATED_SECRET:'never-copy'};
  expect(releaseEnvironment(env)).toMatchObject({DATABASE_URL:env.KENKUI_API_DATABASE_URL,NEON_AUTH_BASE_URL:pin.issuer,NEON_AUTH_JWKS_URL:pin.jwksUrl,KENKUI_NATIVE_CLIENT_ID:pin.clientId,KENKUI_AUTH_RESOURCE:pin.resource,KENKUI_INGRESS_PROTECTION:'true'});
  expect(releaseEnvironment(env)).not.toHaveProperty('UNRELATED_SECRET');
  expect(releaseEnvironment({...env,R2_KEY_SALT:''}).R2_KEY_SALT).toBe('');
  expect(()=>releaseEnvironment({...env,KENKUI_ENV:'production'})).toThrow();
});
