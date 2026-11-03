import { randomUUID } from 'node:crypto';
import { createApi } from './app.js';
import { loadConfig } from './config.js';
import { getPool } from './db/pool.js';
import { IdentityStore } from './db/identities.js';
import { IdentityLinker } from './auth/linking.js';
import { createNeonAuth,loadNeonAuthConfig } from './auth/neon.js';
import { createLegacyVerifier,LegacyRecovery } from './auth/recovery.js';
import { registerAuthRoutes } from './auth/pages.js';
import { AssetStore } from './db/assets.js';
import { ModalBridge } from './compute/modal.js';
import { R2ObjectStore } from './storage/r2.js';
import { registerAssetRoutes, type AssetServices } from './routes/assets.js';

import { BillingStore } from './db/billing.js';
import { Checkout, StripePayments } from './billing/stripe.js';
import { registerBillingRoutes } from './routes/billing.js';
import { JobStore } from './db/jobs.js';
import { NotificationStore } from './db/notifications.js';
import { registerJobRoutes } from './routes/jobs.js';
import { registerNotificationRoutes } from './routes/notifications.js';

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
        let objects:R2ObjectStore|undefined;
        let bridge:ModalBridge|undefined;
        const required=(name:string):string=>{
          const value=process.env[name];
          if(!value)throw new Error('Asset configuration is unavailable');
          return value;
        };
        const assetServices:AssetServices={
          assets:new AssetStore(getPool),
          get objects() {
            return objects??=new R2ObjectStore({
              endpoint:required('R2_ENDPOINT'),bucket:required('R2_BUCKET'),keySalt:process.env.R2_KEY_SALT??'',
              accessKeyId:required('R2_ACCESS_KEY_ID'),secretAccessKey:required('R2_SECRET_ACCESS_KEY'),
            });
          },
          get bridge() {
            return bridge??=new ModalBridge({
              environment:config.environment,url:required('KENKUI_MODAL_BRIDGE_URL'),
              key:required('KENKUI_MODAL_PROXY_KEY'),secret:required('KENKUI_MODAL_PROXY_SECRET'),
              bridgeSecret:required('KENKUI_BRIDGE_SECRET'),
            });
          },
        };
        registerAssetRoutes(next,config,()=>assetServices);
        const billing=new BillingStore(getPool);
        const signingSecret=process.env.STRIPE_WEBHOOK_SECRET;
        let checkout:Checkout|undefined;
        registerBillingRoutes(next,{
          store:billing,checkoutEnabled:Checkout.enabled(process.env),
          checkout:()=>checkout??=Checkout.fromEnvironment(process.env),
          ...(signingSecret?{webhook:new StripePayments(signingSecret,(id,account,credits)=>billing.grantPayment(id,account,credits))}:{}),
        });
        const jobs=new JobStore(getPool);
        registerJobRoutes(next,config,()=>({
          jobs,assets:assetServices.assets,billing,
          get bridge(){return assetServices.bridge as ModalBridge;},
          get objects(){return assetServices.objects;},
          get signals(){return (assetServices.objects as R2ObjectStore).recoverySignals(config.environment);},
        }),Number(process.env.KENKUI_MAX_SPEECH_CHARACTERS??'10000000'));
        registerNotificationRoutes(next,new NotificationStore(getPool),process.env.KENKUI_UNSUBSCRIBE_SECRET??'');
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
