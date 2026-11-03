import type { MiddlewareHandler } from 'hono';
import { ApiError, type ApiEnv } from '../types.js';

/** Hono aliases HEAD to GET. FastAPI's get routes reject HEAD before executing the handler. */
export const getOnly:MiddlewareHandler<ApiEnv>=async(c,next)=>{
  if(c.req.method==='HEAD')throw new ApiError(405,'method_not_allowed','Method not allowed');
  await next();
};
