import { randomUUID } from 'node:crypto';
import { createApi } from './app.js';
import { loadConfig } from './config.js';
import { getPool } from './db/pool.js';

let app:ReturnType<typeof createApi>|undefined;

/** Neon web fetch entrypoint. No env validation, connections or queries on import. */
export default {
  async fetch(request:Request):Promise<Response> {
    if (!app) {
      try {
        app = createApi({config:loadConfig(process.env),getPool,log:event => console.info(JSON.stringify(event))});
      } catch {
        // A failed bootstrap is retryable after configuration is corrected. Never log env.
        const requestId = randomUUID();
        return Response.json({error:{code:'configuration_error',message:'API configuration is unavailable.',details:{},requestId}},{status:503,headers:{'X-Request-ID':requestId}});
      }
    }
    return app.fetch(request);
  },
};
