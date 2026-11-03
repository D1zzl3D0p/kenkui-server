import { randomUUID } from 'node:crypto';
import { createApi } from './app.js';
import { loadConfig } from './config.js';
import { getPool } from './db/pool.js';
import { IdentityStore } from './db/identities.js';
import { Admission } from './auth/admission.js';
import { MigrationChallenges } from './auth/migration-challenge.js';
import { createNeonAuth,loadNeonAuthConfig } from './auth/neon.js';
import { createLegacyVerifier,LegacyRecovery } from './auth/recovery.js';
import { registerAuthRoutes, registerMigrationRoutes } from './auth/pages.js';
import { AssetStore } from './db/assets.js';
import { ModalBridge } from './compute/modal.js';
import { R2ObjectStore } from './storage/r2.js';
import { registerAssetRoutes, type AssetServices } from './routes/assets.js';

import { BillingStore } from './db/billing.js';
import { Checkout, StripePayments } from './billing/stripe.js';
import { registerBillingRoutes } from './routes/billing.js';
import { JobStore } from './db/jobs.js';
import { EventStore } from './db/events.js';
import { registerEventRoutes } from './routes/events.js';
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
        const identities=new IdentityStore(getPool,config.environment);
        const auth=authConfig?createNeonAuth(authConfig,identities):undefined;
        const next=createApi({config,getPool,...(auth?{auth}:{}),log:event => console.info(JSON.stringify(event))});
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
        if(authConfig&&auth) {
          const {environment}=config;
          const {issuer}=authConfig;
          const migration={
            auth,admission:new Admission(getPool,environment,issuer),
            challenges:new MigrationChallenges(getPool,environment,issuer,()=>assetServices.bridge as ModalBridge),
          };
          const legacyIssuer=process.env.KENKUI_LEGACY_TOKEN_ISSUER;
          const recovery=legacyIssuer?new LegacyRecovery(auth,createLegacyVerifier(legacyIssuer),getPool,environment,issuer):undefined;
          registerAuthRoutes(next,authConfig,{migration,...(recovery?{recovery}:{})});
        } else {
          registerMigrationRoutes(next);
        }
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
        registerEventRoutes(next,{jobs,events:new EventStore(getPool)});
        registerNotificationRoutes(next,new NotificationStore(getPool),process.env.KENKUI_UNSUBSCRIBE_SECRET??'');
        app=next;
      } catch {
        // A failed bootstrap is retryable after configuration is corrected. Never log env.
        const requestId = randomUUID();
        return Response.json({error:{code:'configuration_error',message:'API configuration is unavailable.',details:{},requestId}},{status:503,headers:{'X-Request-ID':requestId,...(new URL(request.url).pathname.startsWith('/v1/auth/')?{'Cache-Control':'no-store'}:{})}});
      }
    }
    return app.fetch(request);
  },
};
