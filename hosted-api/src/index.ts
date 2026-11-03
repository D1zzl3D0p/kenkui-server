import { randomUUID } from 'node:crypto';
import { createApi } from './app.js';
import { loadConfig } from './config.js';
import { getPool } from './db/pool.js';
import { IdentityStore } from './db/identities.js';
import { IdentityLinker } from './auth/linking.js';
import { createNeonAuth,loadNeonAuthConfig } from './auth/neon.js';
import { createLegacyVerifier,LegacyRecovery } from './auth/recovery.js';
import { registerAuthRoutes } from './auth/pages.js';

let app:ReturnType<typeof createApi>|undefined;

/** Neon web fetch entrypoint. No env validation, connections or queries on import. */
export default {
  async fetch(request:Request):Promise<Response> {
    if (!app) {
      try {
        const config=loadConfig(process.env);
        const authConfig=loadNeonAuthConfig(process.env,config.allowedOrigins);
        const identities=new IdentityStore(getPool);
        // Task13 must bind an authoritative onboarding decision after reconciliation.
        // Until then unresolved identities fail closed, including potential new users.
        const auth=authConfig?createNeonAuth(authConfig,identities):undefined;
        const next=createApi({config,getPool,...(auth?{auth}:{}),log:event => console.info(JSON.stringify(event))});
        if(authConfig&&auth) {
          const recovery=process.env.KENKUI_LEGACY_TOKEN_ISSUER?new LegacyRecovery(auth,createLegacyVerifier(process.env.KENKUI_LEGACY_TOKEN_ISSUER),identities,new IdentityLinker(getPool),getPool):undefined;
          registerAuthRoutes(next,authConfig,recovery?{recovery}:{});
        }
        app=next;
      } catch {
        // A failed bootstrap is retryable after configuration is corrected. Never log env.
        const requestId = randomUUID();
        return Response.json({error:{code:'configuration_error',message:'API configuration is unavailable.',details:{},requestId}},{status:503,headers:{'X-Request-ID':requestId}});
      }
    }
    return app.fetch(request);
  },
};
