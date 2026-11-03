import type { Hono } from 'hono';
import { ApiError, type ApiEnv, type Catalog } from '../types.js';

export function registerCatalog(app: Hono<ApiEnv>, catalog: Catalog): void {
  app.get('/v1/health', c => c.json({status:'ok'}));
  app.get('/v1/capabilities', c => c.json(catalog.capabilities));
  app.get('/v1/voices', c => c.json({items:catalog.voices}));
  // FastAPI's GET-only catalog routes reject HEAD as well as other verbs.
  for (const path of ['/v1/health','/v1/capabilities','/v1/voices']) {
    app.all(path, () => {throw new ApiError(405,'method_not_allowed','Method not allowed');});
  }
}
