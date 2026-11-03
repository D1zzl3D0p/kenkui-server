/** Opt-in synthetic rehearsal only; existing shared-database helpers stay pinned. */
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { Pool } from 'pg';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { JobStore } from '../src/db/jobs.js';
import { IdentityStore } from '../src/db/identities.js';
import { mailboxKey } from '../src/db/migration.js';
import { jobSpec } from '../src/billing/job-spec.js';

const supplied = process.env.KENKUI_LOCALE_REHEARSAL_URL;
describe.skipIf(!supplied)('paired locale production entry points', () => {
  const owner = '11111111-1111-4111-8111-111111111111';
  const other = '22222222-2222-4222-8222-222222222222';
  const account = '33333333-3333-4333-8333-333333333333';
  let pool: Pool;
  beforeAll(async () => {
    const url = new URL(supplied!);
    // No arbitrary URL, remote host, shared/private port, TLS bypass or release-helper reuse.
    expect(url.protocol).toBe('postgresql:');
    expect(url.hostname).toBe('127.0.0.1');
    expect(['55443', '55444']).toContain(url.port);
    expect(url.pathname).toBe('/postgres');
    expect(url.username).toBe('postgres');
    expect(url.password).toBe('synthetic-locale-only');
    expect(url.searchParams.get('sslmode')).toBe('verify-full');
    expect(url.searchParams.get('sslrootcert')).toBe(process.env.KENKUI_LOCALE_REHEARSAL_CA);
    const ca = readFileSync(process.env.KENKUI_LOCALE_REHEARSAL_CA!, 'utf8');
    pool = new Pool({ host: url.hostname, port: Number(url.port), database: 'postgres',
      user: url.username, password: url.password, ssl: { ca, rejectUnauthorized: true },
      options: '-c search_path=locale_ts', max: 2 });
    expect((await pool.query('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')).rows).toEqual([{ ssl: true }]);
    const row = (await pool.query(`SELECT datlocprovider,datcollate,datctype,datcollversion,
      pg_database_collation_actual_version(oid) AS actual FROM pg_database WHERE datname=current_database()`)).rows[0];
    expect(row).toEqual(url.port === '55443'
      ? { datlocprovider: 'c', datcollate: 'en_US.UTF8', datctype: 'en_US.UTF8', datcollversion: '2.36', actual: '2.36' }
      : { datlocprovider: 'b', datcollate: 'C.UTF-8', datctype: 'C.UTF-8', datcollversion: '1', actual: '1' });
  });
  afterAll(async () => { await pool?.end(); });

  it('keeps provider subjects byte-exact and never transfers existing ownership', async () => {
    const identities = new IdentityStore(() => pool, 'staging');
    for (const [index, subject] of ['Key', 'key', 'é', 'e\u0301'].entries()) {
      const issuer = 'synthetic';
      const expected = index % 2 ? other : owner;
      await pool.query('INSERT INTO provider_identities(issuer,subject,identity_id) VALUES($1,$2,$3)', [issuer, subject, expected]);
      expect(await identities.resolveIdentity(issuer, subject)).toBe(expected);
      expect(await identities.createNewIdentity({ issuer, subject, legacyAccountCheck: 'confirmed-new' })).toBe(expected);
    }
    expect(await identities.resolveIdentity('synthetic', 'KEY')).toBeNull();
    expect(await identities.resolveIdentity('Synthetic', 'Key')).toBeNull();
    expect((await pool.query('SELECT count(*) FROM identities')).rows[0].count).toBe('2');
  });

  it('reserves once per exact owner-scoped key, releases once, and lists canonical UUIDs with owner isolation', async () => {
    const jobs = new JobStore(() => pool);
    const spec = jobSpec({ sourceId: 'owned-source', chapters: ['chapter'], casting: { voiceId: 'voice' } });
    const expected = [
      '00000000-0000-4000-8000-000000000009', '00000000-0000-4000-8000-000000000001',
      '00000000-0000-4000-8000-00000000000a', '00000000-0000-4000-8000-000000000010',
    ];
    for (const [index, key] of ['Key', 'key', 'é', 'e\u0301'].entries()) {
      const input = { jobId: expected[index]!, dispatchId: randomUUID(), ownerId: owner,
        accountId: account, spec, credits: 7n, idempotencyKey: key };
      const created = await jobs.admitJob(input);
      expect(created).toMatchObject({ jobId: expected[index], replayed: false });
      expect(await jobs.admitJob({ ...input, jobId: randomUUID(), dispatchId: randomUUID() }))
        .toMatchObject({ jobId: expected[index], replayed: true });
      expect((await pool.query('SELECT available_credits FROM credit_accounts WHERE id=$1', [account])).rows[0].available_credits).toBe('93');
      await expect(jobs.getOwned(created.jobId, other)).rejects.toMatchObject({ status: 403 });
      await expect(jobs.cancelOwned(created.jobId, other)).rejects.toMatchObject({ status: 403 });
      expect((await jobs.cancelOwned(created.jobId, owner)).status).toBe('cancelled');
      await jobs.cancelOwned(created.jobId, owner);
      expect((await pool.query('SELECT available_credits FROM credit_accounts WHERE id=$1', [account])).rows[0].available_credits).toBe('100');
    }
    expect((await jobs.listOwned(owner)).map(row => row.jobId)).toEqual([
      '00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000009',
      '00000000-0000-4000-8000-00000000000a', '00000000-0000-4000-8000-000000000010',
    ]);
    expect(await jobs.listOwned(other)).toEqual([]);
    expect((await pool.query('SELECT count(*) FROM credit_authorizations')).rows[0].count).toBe('4');
    expect((await pool.query('SELECT kind,count(*) FROM credit_ledger_entries WHERE authorization_id IS NOT NULL GROUP BY kind ORDER BY kind')).rows)
      .toEqual([{ kind: 'release', count: '4' }, { kind: 'reservation', count: '4' }]);
    const denied = { jobId: randomUUID(), dispatchId: randomUUID(), ownerId: owner,
      accountId: account, spec: { ...spec, source_id: 'foreign-source' }, credits: 7n, idempotencyKey: 'foreign' };
    await expect(jobs.admitJob(denied)).rejects.toMatchObject({ status: 403 });
    expect((await pool.query('SELECT available_credits FROM credit_accounts WHERE id=$1', [account])).rows[0].available_credits).toBe('100');
    const otherInput = { ...denied, jobId: randomUUID(), dispatchId: randomUUID(), ownerId: other,
      accountId: '44444444-4444-4444-8444-444444444444', idempotencyKey: 'Key' };
    const separate = await jobs.admitJob(otherInput);
    expect(separate).toMatchObject({ jobId: otherInput.jobId, replayed: false });
    expect(await jobs.admitJob({ ...otherInput, jobId: randomUUID(), dispatchId: randomUUID() }))
      .toMatchObject({ jobId: otherInput.jobId, replayed: true });
    await expect(jobs.getOwned(separate.jobId, owner)).rejects.toMatchObject({ status: 403 });
    expect((await jobs.listOwned(other)).map(row => row.jobId)).toEqual([separate.jobId]);
    expect((await jobs.cancelOwned(separate.jobId, other)).status).toBe('cancelled');
    await jobs.cancelOwned(separate.jobId, other);
    expect((await pool.query('SELECT available_credits FROM credit_accounts WHERE id=$1', [otherInput.accountId])).rows[0].available_credits).toBe('100');
  });

  it('keeps irregular listing membership and owner checks while reporting locale order', async () => {
    const jobs = new JobStore(() => pool);
    const spec = jobSpec({ sourceId: 'owned-source', chapters: ['chapter'], casting: { voiceId: 'voice' } });
    const ids = ['a', 'A', 'a-', 'a_', 'é', 'e\u0301', 'Z', 'z', '!', '１'];
    for (const [index, id] of ids.entries()) {
      await pool.query(`INSERT INTO jobs(id,spec_json,status,version,progress_json,owner_id)
        VALUES($1,$2,'cancelled',0,'{}',$3)`, [id, JSON.stringify(spec), index % 2 ? other : owner]);
    }
    const listed = (await jobs.listOwned(other)).map(row => row.jobId).filter(id => ids.includes(id));
    expect(new Set(listed)).toEqual(new Set(['A', 'a_', 'e\u0301', 'z', '１']));
    await expect(jobs.getOwned('é', other)).rejects.toMatchObject({ status: 403 });
    console.log('IRREGULAR_OWNER_ORDER', JSON.stringify(listed));
  });

  it.each([
    [' User.Name+Tag@Example.INVALID ', 'user.name+tag@example.invalid'],
    ["o'hara!a%test@Sub-Domain.example.invalid", "o'hara!a%test@sub-domain.example.invalid"],
    ['Üser@example.invalid', null], ['user@éxample.invalid', null], ['İ@example.invalid', null],
    ['user@example.invalid\n', null], ['\tuser@example.invalid', null],
    ['user..name@example.invalid', null], ['user@-example.invalid', null],
  ])('normalizes the migration mailbox contract for %j', (input, expected) => {
    expect(mailboxKey(input)).toBe(expected);
  });
});
