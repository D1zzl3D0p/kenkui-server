import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { captureInventory,readPrivateJson,inventoryDigest,captureDigest } from './inventory.mjs';

const requireCheck=(ok,message)=>{if(!ok)throw Error(message);};
const same=isDeepStrictEqual;
export async function assertEmptyTarget(options) {await captureInventory({...options,emptyOnly:true});}
function intact(m) {
  requireCheck(m?.format==='kenkui-restore-inventory-v2'&&/^[a-f0-9]{64}$/.test(m.fullDigest),'Invalid restore inventory');
  requireCheck(inventoryDigest(m)===m.fullDigest,'Restore inventory digest mismatch');
  requireCheck(captureDigest(m)===m.captureDigest,'Capture provenance digest mismatch');
  const time=Date.parse(m.capturedAt);
  requireCheck(Number.isFinite(time)&&time<=Date.now()&&['source','restored-copy','migrated-target'].includes(m.mode),'Invalid capture time or mode');
  requireCheck(Object.keys(m.tables??{}).length>0&&Array.isArray(m.migrationIds),'Incomplete restore inventory');
}
function historical(source,review) {
  const candidates=source.creditInvariants.historicalSettlementCandidates;
  requireCheck(Array.isArray(candidates),'Missing historical allocation evidence');
  if(!candidates.length&&!review.historicalSettlements)return [];
  const h=review.historicalSettlements;
  requireCheck(h?.assertion==='independently-reviewed-historical-pre-0004'&&h.sourceCaptureDigest===source.captureDigest&&same(h.records,candidates)&&candidates.every(r=>r.chronologyValid===true&&r.status==='settled'&&r.finalizedAt&&r.legacyLots.length===1),'Independent historical pre-0004 source attestation required; unresolved chronology blocks');
  return candidates;
}
function healthy(m,approved) {
  const c=m.creditInvariants;
  requireCheck(c&&['orphanReferences','duplicatePayments','invalidLots','crossOwnerAuthorizations','invalidLotAllocations'].every(k=>c[k]===0)&&Array.isArray(c.accounts)&&c.accounts.every(a=>a.availableCredits===a.lotAvailableCredits),'Credit/owner/payment invariant failed');
  requireCheck(c.invalidAllocations===approved.length&&same(c.historicalSettlementCandidates,approved),'Historical/allocation evidence changed or unexplained');
  requireCheck(m.r2?.missingObjects===0&&m.r2?.orphanReferences===0,'R2 reference invariant failed');
  requireCheck(m.managedAuth!=='required'||Object.keys(m.tables).some(k=>k.startsWith('"neon_auth".')),'Managed auth inventory missing');
}
function physical(pin) {return [pin.host,pin.port,pin.database];}
/** Mandatory operator attestation is an existing trust boundary, not cryptographic proof of authorship. */
export function verifyRestore(source,target,review) {
  requireCheck(review?.format==='restore-review-v1'&&['source-stability','restored-copy','migrated-target'].includes(review.purpose),'Independent restore review required');
  requireCheck(['staging','production'].includes(source?.environment)&&source.environment===target?.environment&&review.environment===source.environment,'Restore environment mismatch');
  intact(source);intact(target);
  const reviewedAt=Date.parse(review.attestedAt);
  requireCheck(typeof review.approvalRef==='string'&&!!review.approvalRef.trim()&&typeof review.attestedBy==='string'&&!!review.attestedBy.trim()&&Number.isFinite(reviewedAt)&&reviewedAt>=Math.max(Date.parse(source.capturedAt),Date.parse(target.capturedAt))&&reviewedAt<=Date.now(),'Independent review provenance/time required');
  requireCheck(same(review.source,{target:source.target,captureDigest:source.captureDigest})&&same(review.destination,{target:target.target,captureDigest:target.captureDigest}),'Reviewed source/destination pin or capture mismatch');
  requireCheck(source.mode==='source','Original source capture required');
  if(review.purpose==='source-stability') {
    requireCheck(target.mode==='source'&&same(source.target,target.target)&&source.captureDigest!==target.captureDigest&&Date.parse(source.capturedAt)<Date.parse(target.capturedAt),'Distinct before/after captures of the same source required');
  } else {
    requireCheck(!same(physical(source.target),physical(target.target))&&target.mode===review.purpose,'Independent restored destination required');
  }
  const approved=historical(source,review);healthy(source,approved);healthy(target,approved);
  requireCheck(source.managedAuth===target.managedAuth||(review.purpose==='migrated-target'&&review.managedAuthAdded===true&&source.managedAuth==='absent-reviewed-source'&&target.managedAuth==='required'),'Managed auth scope mismatch');
  if(review.purpose!=='migrated-target') {
    for(const key of ['databaseCollation','migrationIds','tables','schemaObjects','sequences','creditInvariants','r2','fullDigest'])requireCheck(same(source[key],target[key]),'Source-copy migration/table/schema/digest mismatch');
    return {status:review.purpose==='source-stability'?'verified-source-stability':'verified-copy',tables:Object.keys(target.tables).length,fullDigest:target.fullDigest,captureDigest:target.captureDigest};
  }
  requireCheck(/^[a-f0-9]{64}$/.test(review.reviewedTargetDigest)&&review.reviewedTargetDigest===target.fullDigest,'Full target attestation required after DDL');
  requireCheck(Array.isArray(review.migrationIds)&&new Set(review.migrationIds).size===review.migrationIds.length&&same([...source.migrationIds,...review.migrationIds].sort(),target.migrationIds),'Unknown migration delta');
  requireCheck(target.sourceProjectionCaptureDigest===source.captureDigest,'Original source projection provenance mismatch');
  for(const [name,table] of Object.entries(source.tables)) {
    if(name==='"public"."schema_migrations"')continue;
    requireCheck(same(target.sourceProjection?.[name],{count:table.count,sha256:table.sha256}),'Original source rows not preserved after DDL');
  }
  for(const [name,sequence] of Object.entries(source.sequences))requireCheck(same(sequence,target.sequences[name]),'Original sequence definition/ownership/state changed');
  const added=Object.fromEntries(Object.entries(target.sequences).filter(([key])=>!Object.hasOwn(source.sequences,key)));
  requireCheck(review.newSequences&&same(review.newSequences,added),'Independent exact new-sequence review required');
  requireCheck(same(source.creditInvariants,target.creditInvariants)&&same(source.r2,target.r2),'Original financial/R2 facts not preserved');
  return {status:'verified-migration',tables:Object.keys(target.tables).length,fullDigest:target.fullDigest,captureDigest:target.captureDigest,restoredSnapshotDigest:target.restoredSnapshotDigest};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const [action,first,second,third,...extra]=process.argv.slice(2);if(extra.length)throw Error();
    if(action==='assert-empty'&&first&&!second) {await assertEmptyTarget({...await readPrivateJson(first),databaseUrl:process.env.KENKUI_INVENTORY_DATABASE_URL});console.log('Empty target verified; no database changes made.');}
    else if(action==='compare'&&first&&second&&third)console.log(JSON.stringify(verifyRestore(await readPrivateJson(first),await readPrivateJson(second),await readPrivateJson(third))));
    else throw Error();
  } catch {console.error('Restore verification failed; keep writers frozen and review private evidence.');process.exitCode=1;}
}
