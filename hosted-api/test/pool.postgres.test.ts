import { once } from 'node:events';
import { Client, Pool } from 'pg';
import { expect, it } from 'vitest';
import { createLazyPool } from '../src/db/pool.js';

const testUrl = process.env.KENKUI_TEST_DATABASE_URL;

it.skipIf(!testUrl)('recovers after a real idle backend disconnect, closes idle sockets and can reopen after explicit close', async () => {
  // Synthetic disposable instance only. Never permit provider or non-loopback URLs.
  const url = new URL(testUrl!);
  expect(url.hostname).toBe('127.0.0.1');
  expect(url.port).toBe('55438');
  expect(url.username).toBe('postgres');
  expect(url.pathname).toBe('/postgres');
  const admin = new Client({connectionString:testUrl!,ssl:false});
  const lazy = createLazyPool(() => ({connectionString:testUrl!}),{
    // Only the synthetic local server lacks TLS. Production options have separate tests.
    factory: options => new Pool({...options,ssl:false}),
  });
  try {
    await admin.connect();
    const pool = lazy.getPool();
    const first = (await pool.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
    const disconnected = once(pool,'error');
    await admin.query('SELECT pg_terminate_backend($1)',[first]);
    await disconnected;
    const second = (await pool.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
    expect(second).not.toBe(first);
    const removed = once(pool,'remove');
    await removed; // Exercise the actual 10-second production idle timeout.
    expect(pool.totalCount).toBe(0);
    const third = (await pool.query('SELECT pg_backend_pid() AS pid')).rows[0].pid;
    expect(third).not.toBe(second);
    await lazy.close();
    await expect(pool.query('SELECT 1')).rejects.toThrow('end');
    expect((await lazy.getPool().query('SELECT 1 AS value')).rows[0].value).toBe(1);
  } finally {
    await lazy.close();
    await admin.end();
  }
},20_000);
