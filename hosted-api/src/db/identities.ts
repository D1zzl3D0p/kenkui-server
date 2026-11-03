import { randomUUID } from 'node:crypto';
import type { Pool, PoolClient } from 'pg';

export type IdentityErrorCode = 'invalid_identity' | 'unknown_identity' | 'invalid_proof'
  | 'identity_conflict' | 'legacy_recovery_required' | 'source_authority_mismatch' | 'ambiguous_source';

/** Internal domain errors. HTTP handlers must map these to safe public errors. */
export class IdentityError extends Error {
  constructor(readonly code: IdentityErrorCode) {
    super(code);
    this.name = 'IdentityError';
  }
}

export function validateProviderIdentity(issuer: string, subject: string): void {
  if (typeof issuer !== 'string' || !issuer.trim() || issuer.includes('\0')
    || typeof subject !== 'string' || !subject.trim() || subject.includes('\0')) {
    throw new IdentityError('invalid_identity');
  }
  // No email/username joins or implicit issuer/subject normalization. Task 5
  // verifies each token type before deliberately choosing a canonical authority.
}

export async function findIdentity(client: Pool | PoolClient, issuer: string, subject: string): Promise<string | null> {
  const result = await client.query<{identity_id: string}>(
    'SELECT identity_id FROM provider_identities WHERE issuer=$1 AND subject=$2', [issuer, subject]);
  return result.rows[0]?.identity_id ?? null;
}

export async function lockIdentity(client:PoolClient,issuer:string,subject:string):Promise<void> {
  await client.query('SELECT pg_advisory_xact_lock(915002,hashtext($1))',[JSON.stringify([issuer,subject])]);
}

export async function createIdentity(client:PoolClient,issuer:string,subject:string,email:string|null):Promise<string> {
  validateProviderIdentity(issuer,subject);
  await lockIdentity(client,issuer,subject);
  const existing=await findIdentity(client,issuer,subject);
  if(existing) return existing;
  const owner=randomUUID();
  await client.query('INSERT INTO identities (id,email) VALUES ($1,$2)',[owner,email]);
  await bindIdentity(client,issuer,subject,owner);
  return owner;
}

/** Insert-only: a provider identity can never be reassigned to another owner. */
async function bindIdentity(client: PoolClient, issuer: string, subject: string, owner: string): Promise<void> {
  await client.query(`INSERT INTO provider_identities (issuer,subject,identity_id) VALUES ($1,$2,$3)
    ON CONFLICT (issuer,subject) DO NOTHING`, [issuer, subject, owner]);
  if (await findIdentity(client, issuer, subject) !== owner) throw new IdentityError('identity_conflict');
}

export interface WorkosSourceAuthority { environmentId: string; clientId: string }
export interface WorkosBackfillInput {
  authority: WorkosSourceAuthority;
  /** Exact IDs from the reconciled authoritative WorkOS export, never email matches. */
  subjects: readonly string[];
  dryRun: boolean;
}
export interface WorkosBackfillResult {
  issuer: string;
  matched: number;
  existing: number;
  unmatchedSubjects: string[];
  missingFromSource: string[];
}

/** Server-internal SQL primitives. No lookup miss creates an account. */
export class IdentityStore {
  constructor(private readonly getPool: () => Pool) {}

  async resolveIdentity(issuer: string, subject: string): Promise<string | null> {
    validateProviderIdentity(issuer, subject);
    return findIdentity(this.getPool(), issuer, subject);
  }

  /** Task 5 must rule out legacy recovery BEFORE calling this explicit primitive.
   * This decision is not a client input or proof of authentication. Never expose
   * it as an HTTP body field. No provider subject is fabricated for WorkOS.
   */
  async createNewIdentity(input: {
    issuer: string; subject: string; legacyAccountCheck: 'confirmed-new';
  }): Promise<string> {
    validateProviderIdentity(input.issuer, input.subject);
    if (input.legacyAccountCheck !== 'confirmed-new') throw new IdentityError('legacy_recovery_required');
    const client = await this.getPool().connect();
    try {
      await client.query('BEGIN');
      const owner=await createIdentity(client,input.issuer,input.subject,null);
      await client.query('COMMIT');
      return owner;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }

  /** Offline release helper. The trusted authority is server-owned configuration,
   * checked independently of the imported source. No provider access or export is
   * performed here. Unmatched records require explicit reconciliation/recovery.
   */
  workosBackfill(trustedSource: WorkosSourceAuthority): (input: WorkosBackfillInput) => Promise<WorkosBackfillResult> {
    const {environmentId, clientId} = trustedSource;
    if (!/^environment_[A-Za-z0-9]+$/.test(environmentId) || !/^client_[A-Za-z0-9]+$/.test(clientId)) {
      throw new IdentityError('source_authority_mismatch');
    }
    const issuer = `urn:kenkui:workos:${environmentId}:${clientId}`;
    return async input => {
      if (input.authority.environmentId !== environmentId || input.authority.clientId !== clientId
        || typeof input.dryRun !== 'boolean') throw new IdentityError('source_authority_mismatch');
      const subjects = [...input.subjects];
      for (const subject of subjects) validateProviderIdentity(issuer, subject);
      if (new Set(subjects).size !== subjects.length) throw new IdentityError('ambiguous_source');
      const client = await this.getPool().connect();
      try {
        await client.query('BEGIN');
        // Offline only. Stabilize legacy IDs/subjects while making the plan/apply
        // decision; never hold this transaction across any provider calls.
        await client.query('LOCK TABLE identities IN SHARE ROW EXCLUSIVE MODE');
        const legacy = await client.query<{id: string; workos_subject: string}>(
          'SELECT id,workos_subject FROM identities WHERE workos_subject IS NOT NULL ORDER BY workos_subject');
        const owners = new Map(legacy.rows.map(row => [row.workos_subject, row.id]));
        const source = new Set(subjects);
        const result: WorkosBackfillResult = {issuer, matched:0, existing:0, unmatchedSubjects:[],
          missingFromSource:legacy.rows.filter(row => !source.has(row.workos_subject)).map(row => row.workos_subject)};
        for (const subject of subjects.sort()) {
          const owner = owners.get(subject);
          if (!owner) {
            result.unmatchedSubjects.push(subject);
            continue;
          }
          result.matched++;
          const existing = await findIdentity(client, issuer, subject);
          if (existing && existing !== owner) throw new IdentityError('identity_conflict');
          if (existing) result.existing++;
          if (!input.dryRun) await bindIdentity(client, issuer, subject, owner);
        }
        await client.query('COMMIT');
        return result;
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      } finally {
        client.release();
      }
    };
  }
}
