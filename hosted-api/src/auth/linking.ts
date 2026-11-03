import type { Pool,PoolClient } from 'pg';
import type { Environment } from '../db/migration.js';
import { findIdentity, lockDirectory, lockIdentity, IdentityError, validateProviderIdentity } from '../db/identities.js';

export interface LinkIdentityInput {
  issuer: string;
  subject: string;
  internalUserId: string;
  proofId: string;
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function linkOnClient(client:PoolClient,input:LinkIdentityInput,consumeFresh=false):Promise<string> {
  const {issuer, subject, proofId} = input;
  validateProviderIdentity(issuer, subject);
  if (!uuid.test(input.internalUserId)) throw new IdentityError('unknown_identity');
  if (!uuid.test(proofId)) throw new IdentityError('invalid_proof');
  const owner = input.internalUserId.toLowerCase();
  await lockIdentity(client,issuer,subject);
  const user = await client.query('SELECT id FROM identities WHERE id=$1 FOR KEY SHARE', [owner]);
  if (!user.rowCount) throw new IdentityError('unknown_identity');
  const proof = await client.query<{usable: boolean}>(`SELECT
    consumed_at IS NULL AND verified_at <= clock_timestamp() AND expires_at > clock_timestamp() AS usable
    FROM identity_link_proofs
    WHERE id=$1 AND identity_id=$2 AND issuer=$3 AND subject=$4 FOR UPDATE`,
  [proofId, owner, issuer, subject]);
  if (!proof.rowCount) throw new IdentityError('invalid_proof');
  const existing = await findIdentity(client, issuer, subject);
  if (existing) {
    if (existing !== owner) throw new IdentityError('identity_conflict');
    // Exact, previously proved binding: safe read-only retry, even if its
    // bound proof is consumed/expired. It cannot create or change anything.
    if(!consumeFresh) return existing;
  }
  if (!proof.rows[0]!.usable) throw new IdentityError('invalid_proof');
  await client.query(`INSERT INTO provider_identities (issuer,subject,identity_id) VALUES ($1,$2,$3)
    ON CONFLICT (issuer,subject) DO NOTHING`, [issuer, subject, owner]);
  if (await findIdentity(client, issuer, subject) !== owner) throw new IdentityError('identity_conflict');
  // Check wall clock again after possible unique-key contention. Transaction
  // start time (now()) would incorrectly extend proof lifetime while waiting.
  const consumed = await client.query(`UPDATE identity_link_proofs SET consumed_at=clock_timestamp()
    WHERE id=$1 AND consumed_at IS NULL AND verified_at <= clock_timestamp()
      AND expires_at > clock_timestamp() RETURNING id`, [proofId]);
  if (!consumed.rowCount) throw new IdentityError('invalid_proof');
  return owner;
}

/** Only consumes proof recorded by the verified server flow. This is not an HTTP
 * endpoint or a verifier: Task 5 must authenticate issuer+subject before calling.
 * No caller-controlled internalUserId can mint a proof or transfer an account.
 */
export class IdentityLinker {
  constructor(private readonly getPool: () => Pool, private readonly environment:Environment) {}

  async linkIdentity(input: LinkIdentityInput): Promise<string> {
    const client=await this.getPool().connect();
    try {
      await client.query('BEGIN');
      await lockDirectory(client,this.environment);
      const owner=await linkOnClient(client,input);
      await client.query('COMMIT');
      return owner;
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  }
}
