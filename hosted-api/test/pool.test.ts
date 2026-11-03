import { expect, it, vi } from 'vitest';
import { Client, Pool, type PoolConfig } from 'pg';
import { createLazyPool } from '../src/db/pool.js';

it('constructs lazily without a query and keeps max 3, bounded idle time and verified TLS even with sslmode=require', async () => {
  const made: Pool[] = [];
  const factory = vi.fn((options: PoolConfig) => {const p = new Pool(options); made.push(p); return p;});
  const lazy = createLazyPool(() => ({connectionString:'postgresql://user:synthetic@localhost/db?sslmode=require&uselibpqcompat=true&sslrootcert=/missing-test-ca&sslnegotiation=direct&application_name=kenkui'}), {factory});
  expect(factory).not.toHaveBeenCalled();
  const p = lazy.getPool();
  expect(lazy.getPool()).toBe(p);
  expect(factory).toHaveBeenCalledTimes(1);
  expect(p.options.max).toBe(3);
  expect(p.options.idleTimeoutMillis).toBe(10_000);
  expect(p.options.connectionTimeoutMillis).toBe(5_000);
  expect(p.options.ssl).toEqual({rejectUnauthorized:true});
  expect(p.options.connectionString).not.toMatch(/sslmode|uselibpqcompat|sslrootcert|sslnegotiation/);
  expect((new Client(p.options) as unknown as {connectionParameters:{ssl:unknown}}).connectionParameters.ssl).toEqual({rejectUnauthorized:true});
  expect(p.options.connectionString).toContain('application_name=kenkui');
  expect(p.totalCount).toBe(0);
  // Integration is real: an expected idle disconnect must not become an unhandled EventEmitter error.
  expect(() => p.emit('error', Object.assign(new Error('idle'),{code:'ECONNRESET'}))).not.toThrow();
  await lazy.close();
  expect(lazy.getPool()).not.toBe(p);
  await lazy.close();
});

it('reports unexpected idle errors without exposing connection credentials', async () => {
  const events: unknown[] = [];
  const lazy = createLazyPool(() => ({connectionString:'postgresql://user:secret@localhost/db'}),{log:e => events.push(e)});
  const p = lazy.getPool();
  p.emit('error',new Error('postgresql://user:secret@localhost/db'));
  expect(events).toEqual([{event:'database.pool_error'}]);
  await lazy.close();
});

it('does not poison lazy construction when configuration is temporarily unavailable', async () => {
  let ready = false;
  const lazy = createLazyPool(() => {if(!ready) throw new Error('not configured'); return {connectionString:'postgresql://localhost/db'};});
  expect(() => lazy.getPool()).toThrow('not configured');
  ready = true;
  expect(lazy.getPool().totalCount).toBe(0);
  await lazy.close();
});

it('rejects absent or malformed database URLs before construction', () => {
  for (const connectionString of ['', 'https://example.invalid', 'postgresql:///db']) {
    expect(() => createLazyPool(() => ({connectionString})).getPool()).toThrow('database');
  }
});
