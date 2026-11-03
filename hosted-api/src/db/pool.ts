import { Pool, type PoolConfig } from 'pg';
import { attachDatabasePool } from '@neon/functions';
import type { Logger } from '../types.js';

export interface DatabaseConfig { connectionString: string }
export interface LazyPool { getPool(): Pool; close(): Promise<void> }
export interface PoolDependencies { factory?: (config: PoolConfig) => Pool; log?: Logger }

function verifiedOptions(config: DatabaseConfig): PoolConfig {
  let url:URL;
  try {url = new URL(config.connectionString);} catch {throw new Error('A valid database URL is required');}
  if (!['postgres:','postgresql:'].includes(url.protocol) || !url.hostname) throw new Error('A valid database URL is required');
  // pg-connection-string otherwise replaces the ssl object with URL sslmode options.
  for (const key of [...url.searchParams.keys()]) {
    if (key.startsWith('ssl') || key === 'uselibpqcompat') url.searchParams.delete(key);
  }
  return {connectionString:url.toString(),ssl:{rejectUnauthorized:true},max:3,idleTimeoutMillis:10_000,connectionTimeoutMillis:5_000,allowExitOnIdle:true};
}

export function createLazyPool(config: () => DatabaseConfig, deps:PoolDependencies = {}): LazyPool {
  let pool:Pool|undefined;
  return {
    getPool() {
      if (!pool) {
        const created = (deps.factory ?? (options => new Pool(options)))(verifiedOptions(config()));
        // Report event metadata only: pg errors can contain URLs or SQL parameters.
        attachDatabasePool(created,{onUnexpectedError:() => deps.log?.({event:'database.pool_error'})});
        pool = created;
      }
      return pool;
    },
    async close() {
      const closing = pool;
      pool = undefined;
      if (closing) await closing.end();
    },
  };
}

const sharedPool = createLazyPool(() => ({connectionString:process.env.KENKUI_API_DATABASE_URL ?? ''}),{log:e => console.error(JSON.stringify(e))});
export const getPool = (): Pool => sharedPool.getPool();
export const closePool = (): Promise<void> => sharedPool.close();
