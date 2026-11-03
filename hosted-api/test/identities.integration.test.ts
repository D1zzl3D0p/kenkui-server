import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { promisify } from 'node:util';
import { Client, Pool } from 'pg';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { IdentityStore } from '../src/db/identities.js';
import { IdentityLinker } from '../src/auth/linking.js';

const run = promisify(execFile);
const testUrl = process.env.KENKUI_TEST_DATABASE_URL;
const owner = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const account = '33333333-3333-4333-8333-333333333333';
const issuer = 'https://auth.synthetic.invalid/neon';
const subject = "user'; DROP TABLE identities; --";
const authority = { environmentId: 'environment_synthetic', clientId: 'client_synthetic' };

describe.skipIf(!testUrl)('durable provider identity mapping on real PostgreSQL', () => {
  let admin: Client;
  let pool: Pool;
  let schema: string;
  async function python(operation: string) {
    return run('../.venv/bin/python', ['test/helpers/identity_database.py', schema, operation], {
      env: { ...process.env, KENKUI_TEST_DATABASE_URL: testUrl! },
    });
  }
  async function services() {
    return { store: new IdentityStore(() => pool), linker: new IdentityLinker(() => pool) };
  }
  async function proof(idOwner = owner, idSubject = subject, idIssuer = issuer) {
    const id = randomUUID();
    // Test-only verified fixture. Production issuance belongs to Task 5's verifier.
    await pool.query(`INSERT INTO identity_link_proofs
      (id, identity_id, issuer, subject, verification_method, expires_at)
      VALUES ($1,$2,$3,$4,'legacy_session',clock_timestamp()+interval '5 minutes')`,
    [id, idOwner, idIssuer, idSubject]);
    return id;
  }
  async function snapshot() {
    const tables = ['identities','jobs','dispatches','credit_accounts','credit_authorizations',
      'credit_ledger_entries','credit_lots','credit_allocations','payment_events'];
    return Promise.all(tables.map(async table => (await pool.query(`SELECT * FROM ${table}`)).rows));
  }
  async function addOther() {
    await pool.query('INSERT INTO identities (id,workos_subject,email) VALUES ($1,$2,$3)',
      [other, 'other-legacy', 'old@example.invalid']);
  }
  // Force actual overlapping database statements behind a held table lock. Seeing
  // both backend waiters avoids the false confidence of Promise.all alone.
  async function overlap<T>(table: string, actions: (() => Promise<T>)[], whileBlocked?: () => Promise<void>) {
    const blocker = await pool.connect();
    await blocker.query('BEGIN');
    await blocker.query(`LOCK TABLE ${table} IN ACCESS EXCLUSIVE MODE`);
    const pending = Promise.allSettled(actions.map(action => action()));
    try {
      await expect.poll(async () => Number((await admin.query(`SELECT count(*) FROM pg_stat_activity
        WHERE application_name=$1 AND wait_event_type='Lock' AND pid<>$2`,
      [schema, (await blocker.query('SELECT pg_backend_pid() AS pid')).rows[0].pid])).rows[0].count),
      { timeout: 5000 }).toBe(actions.length);
      await whileBlocked?.();
    } finally {
      await blocker.query('ROLLBACK');
      blocker.release();
    }
    return pending;
  }
  beforeEach(async () => {
    expect(testUrl).toBe('postgresql://postgres@127.0.0.1:55438/postgres');
    schema = `identity_test_${randomUUID().replaceAll('-', '')}`;
    admin = new Client({ connectionString: testUrl!, ssl: false });
    await admin.connect();
    await admin.query(`CREATE SCHEMA ${schema}`);
    pool = new Pool({ connectionString: testUrl!, ssl: false, max: 5,
      options: `-c search_path=${schema}`, application_name: schema });
    await python('initialize');
  });
  afterEach(async () => {
    await pool?.end();
    if (admin) {
      if (schema) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
      await admin.end();
    }
  });

  it('upgrades old rows without replacing ownership and keeps the legacy unique constraint', async () => {
    expect((await pool.query(`SELECT is_nullable FROM information_schema.columns
      WHERE table_schema=$1 AND table_name='identities' AND column_name='workos_subject'`, [schema])).rows)
      .toEqual([{ is_nullable: 'YES' }]);
    expect((await pool.query('SELECT identity_id,available_credits FROM credit_accounts')).rows)
      .toEqual([{ identity_id: owner, available_credits: '13' }]);
    expect((await pool.query('SELECT owner_id FROM jobs')).rows).toEqual([{owner_id: owner}]);
    await expect(pool.query('INSERT INTO identities(id,workos_subject) VALUES ($1,$2)',
      [randomUUID(), 'legacy-subject'])).rejects.toMatchObject({code: '23505'});
  });

  it('links only a bound verified proof, preserves every old row, and retries read-only after expiry', async () => {
    const {store, linker} = await services();
    const before = await snapshot();
    expect(await store.resolveIdentity(issuer, subject)).toBeNull();
    const proofId = await proof();
    const input = {issuer, subject, internalUserId: owner, proofId};
    expect(await linker.linkIdentity(input)).toBe(owner);
    expect(await store.resolveIdentity(issuer, subject)).toBe(owner);
    expect(await store.resolveIdentity(issuer + '/different', subject)).toBeNull();
    expect(await snapshot()).toEqual(before);
    const consumed = (await pool.query('SELECT consumed_at FROM identity_link_proofs')).rows[0].consumed_at;
    expect(consumed).toBeInstanceOf(Date);
    await pool.query(`UPDATE identity_link_proofs SET verified_at=clock_timestamp()-interval '2 hours',
      expires_at=clock_timestamp()-interval '1 hour' WHERE id=$1`, [proofId]);
    expect(await linker.linkIdentity(input)).toBe(owner);
    expect((await pool.query('SELECT consumed_at FROM identity_link_proofs')).rows[0].consumed_at).toEqual(consumed);
    const aliasIssuer = issuer + '/native';
    expect(await linker.linkIdentity({...input, issuer: aliasIssuer,
      proofId: await proof(owner, subject, aliasIssuer)})).toBe(owner);
  });

  it('rejects expired, consumed, missing and mismatched proofs without email or username joins', async () => {
    const {store, linker} = await services();
    await addOther();
    const expired = await proof();
    await pool.query(`UPDATE identity_link_proofs SET verified_at=clock_timestamp()-interval '2 hours',
      expires_at=clock_timestamp()-interval '1 hour' WHERE id=$1`, [expired]);
    const consumed = await proof();
    await pool.query('UPDATE identity_link_proofs SET consumed_at=clock_timestamp() WHERE id=$1', [consumed]);
    for (const proofId of [expired, consumed, randomUUID(), await proof(other), await proof(owner, 'another')]) {
      await expect(linker.linkIdentity({issuer,subject,internalUserId: owner,proofId}))
        .rejects.toMatchObject({code: 'invalid_proof'});
    }
    expect(await store.resolveIdentity(issuer, 'old@example.invalid')).toBeNull();
    expect(await store.resolveIdentity(issuer, 'legacy-subject')).toBeNull();
    expect((await pool.query('SELECT count(*) FROM provider_identities')).rows[0].count).toBe('0');
    await expect(linker.linkIdentity({issuer,subject,internalUserId: randomUUID(),proofId: await proof()}))
      .rejects.toMatchObject({code: 'unknown_identity'});
  });

  it('prevents consumed proof replay to a different subject or owner and refuses account reassignment', async () => {
    const {linker} = await services();
    await addOther();
    const proofId = await proof();
    await linker.linkIdentity({issuer,subject,internalUserId: owner,proofId});
    await expect(linker.linkIdentity({issuer,subject: 'changed',internalUserId: owner,proofId}))
      .rejects.toMatchObject({code:'invalid_proof'});
    await expect(linker.linkIdentity({issuer,subject,internalUserId: other,proofId}))
      .rejects.toMatchObject({code:'invalid_proof'});
    const otherProof = await proof(other);
    await expect(linker.linkIdentity({issuer,subject,internalUserId: other,proofId: otherProof}))
      .rejects.toMatchObject({code:'identity_conflict'});
    expect((await pool.query('SELECT consumed_at FROM identity_link_proofs WHERE id=$1',[otherProof])).rows[0].consumed_at).toBeNull();
  });

  it('serializes conflicting links while consuming only the winning proof', async () => {
    const {store, linker} = await services();
    await addOther();
    const ids = [await proof(), await proof(other)];
    const results = await overlap('provider_identities', [owner,other].map((id,index) =>
      () => linker.linkIdentity({issuer,subject,internalUserId:id,proofId:ids[index]!})));
    expect(results.filter(r => r.status==='fulfilled')).toHaveLength(1);
    expect(results.filter(r => r.status==='rejected')).toEqual([
      expect.objectContaining({reason:expect.objectContaining({code:'identity_conflict'})}),
    ]);
    const winner = await store.resolveIdentity(issuer,subject);
    expect([owner,other]).toContain(winner);
    expect((await pool.query('SELECT identity_id FROM identity_link_proofs WHERE consumed_at IS NOT NULL')).rows)
      .toEqual([{identity_id:winner}]);
  });

  it('does not extend proof lifetime while a database lock delays the link', async () => {
    const {store, linker} = await services();
    const proofId = await proof();
    const expiry = (await pool.query(`UPDATE identity_link_proofs
      SET expires_at=clock_timestamp()+interval '2 seconds' WHERE id=$1 RETURNING expires_at`,
    [proofId])).rows[0].expires_at;
    const results = await overlap('provider_identities', [
      () => linker.linkIdentity({issuer,subject,internalUserId:owner,proofId}),
    ], async () => {
      await expect.poll(async () => (await admin.query('SELECT clock_timestamp()>$1 AS expired', [expiry])).rows[0].expired,
        {timeout:4000}).toBe(true);
    });
    expect(results).toEqual([{status:'rejected', reason:expect.objectContaining({code:'invalid_proof'})}]);
    expect(await store.resolveIdentity(issuer,subject)).toBeNull();
    expect((await pool.query('SELECT consumed_at FROM identity_link_proofs WHERE id=$1',[proofId])).rows[0].consumed_at).toBeNull();
  });

  it('serializes retries of the same proof and never recreates a removed binding with consumed proof', async () => {
    const {store, linker} = await services();
    const proofId = await proof();
    const input = {issuer,subject,internalUserId:owner,proofId};
    const results = await overlap('provider_identities', [() => linker.linkIdentity(input), () => linker.linkIdentity(input)]);
    expect(results).toEqual([{status:'fulfilled',value:owner},{status:'fulfilled',value:owner}]);
    await pool.query('DELETE FROM provider_identities WHERE issuer=$1 AND subject=$2',[issuer,subject]);
    await expect(linker.linkIdentity(input)).rejects.toMatchObject({code:'invalid_proof'});
    expect(await store.resolveIdentity(issuer,subject)).toBeNull();
  });

  it('enforces existing owners for both mappings and server-verified proofs at the SQL boundary', async () => {
    await expect(pool.query('INSERT INTO provider_identities(issuer,subject,identity_id) VALUES($1,$2,$3)',
      [issuer,subject,randomUUID()])).rejects.toMatchObject({code:'23503'});
    await expect(proof(randomUUID())).rejects.toMatchObject({code:'23503'});
  });

  it('rolls back an inserted mapping when proof consumption fails', async () => {
    const {store, linker} = await services();
    const proofId = await proof();
    // Fault injection at the real SQL transaction boundary, no mocked query results.
    await pool.query('ALTER TABLE identity_link_proofs ADD CONSTRAINT synthetic_fail CHECK(consumed_at IS NULL)');
    await expect(linker.linkIdentity({issuer,subject,internalUserId:owner,proofId})).rejects.toThrow();
    expect(await store.resolveIdentity(issuer,subject)).toBeNull();
    expect((await pool.query('SELECT consumed_at FROM identity_link_proofs')).rows[0].consumed_at).toBeNull();
    await pool.query('ALTER TABLE identity_link_proofs DROP CONSTRAINT synthetic_fail');
    expect(await linker.linkIdentity({issuer,subject,internalUserId:owner,proofId})).toBe(owner);
  });

  it('creates one Neon-only account during overlapping retries with no orphan identities', async () => {
    const {store} = await services();
    // Both requests first observe no mapping, then block at INSERT identities.
    // This forces the losing INSERT-binding path to roll back its new identity.
    const results = await overlap('identities', [0,1].map(() =>
      () => store.createNewIdentity({issuer,subject,legacyAccountCheck:'confirmed-new'})));
    expect(results[0]).toEqual(results[1]);
    expect(results[0]!.status).toBe('fulfilled');
    const id = await store.resolveIdentity(issuer,subject);
    expect(id).not.toBe(owner);
    expect((await pool.query('SELECT id,workos_subject,email FROM identities WHERE id<>$1',[owner])).rows)
      .toEqual([{id,workos_subject:null,email:null}]);
    expect((await pool.query('SELECT count(*) FROM identities')).rows[0].count).toBe('2');
  });

  it('requires a deliberate new-account decision and rejects empty provider identities', async () => {
    const {store} = await services();
    await expect(store.createNewIdentity({issuer,subject,legacyAccountCheck:'recovery-needed'} as never))
      .rejects.toMatchObject({code:'legacy_recovery_required'});
    await expect(store.resolveIdentity('',subject)).rejects.toMatchObject({code:'invalid_identity'});
    expect((await pool.query('SELECT count(*) FROM identities')).rows[0].count).toBe('1');
  });

  it('dry-runs a trusted WorkOS source and backfills by exact subject, preserving existing UUIDs', async () => {
    const {store} = await services();
    await addOther();
    const backfill = store.workosBackfill(authority);
    const input = {authority, subjects:['legacy-subject','no-database-account']};
    const expected = {issuer:'urn:kenkui:workos:environment_synthetic:client_synthetic',
      matched:1, existing:0, unmatchedSubjects:['no-database-account'], missingFromSource:['other-legacy']};
    expect(await backfill({...input,dryRun:true})).toEqual(expected);
    expect((await pool.query('SELECT count(*) FROM provider_identities')).rows[0].count).toBe('0');
    expect(await backfill({...input,dryRun:false})).toEqual(expected);
    expect(await store.resolveIdentity(expected.issuer,'legacy-subject')).toBe(owner);
    expect(await backfill({...input,dryRun:false})).toEqual({...expected,existing:1});
    await expect(backfill({...input,authority:{...authority,clientId:'client_wrong'},dryRun:false}))
      .rejects.toMatchObject({code:'source_authority_mismatch'});
  });

  it('rolls back a whole backfill on conflicting ownership and rejects ambiguous duplicate input', async () => {
    const {store} = await services();
    await addOther();
    const oldIssuer = 'urn:kenkui:workos:environment_synthetic:client_synthetic';
    await pool.query('INSERT INTO provider_identities(issuer,subject,identity_id) VALUES($1,$2,$3)',
      [oldIssuer,'other-legacy',owner]);
    const backfill = store.workosBackfill(authority);
    await expect(backfill({authority,subjects:['legacy-subject','other-legacy'],dryRun:false}))
      .rejects.toMatchObject({code:'identity_conflict'});
    expect(await store.resolveIdentity(oldIssuer,'legacy-subject')).toBeNull();
    await expect(backfill({authority,subjects:['legacy-subject','legacy-subject'],dryRun:true}))
      .rejects.toMatchObject({code:'ambiguous_source'});
  });

  it('round-trips UUIDs, BIGINT and JSON text through TypeScript and the old Python repositories', async () => {
    const {store} = await services();
    const neonOwner = await store.createNewIdentity({issuer,subject,legacyAccountCheck:'confirmed-new'});
    await pool.query('UPDATE credit_accounts SET available_credits=$1 WHERE id=$2',['9007199254740993',account]);
    await pool.query('UPDATE jobs SET progress_json=$1 WHERE id=$2',
      ['{"stage":"章-é","completed":9007199254740993,"total":9223372036854775806}','book-before']);
    const result = JSON.parse((await python('roundtrip')).stdout);
    expect(result).toEqual({owner,neonOwner,credits:'9007199254740993'});
    expect((await pool.query('SELECT available_credits FROM credit_accounts WHERE id=$1',[account])).rows[0].available_credits)
      .toBe('9223372036854775806');
    expect((await pool.query('SELECT progress_json FROM jobs WHERE id=$1',['book-before'])).rows[0].progress_json)
      .toBe('{"stage": "章-é", "completed": 9007199254740994, "total": 9223372036854775806}');
  });
});
