import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { captureInventory,readPrivateJson,inventoryDigest } from './inventory.mjs';

const requireCheck=(ok,message)=>{if(!ok)throw Error(message);};
export async function assertEmptyTarget(options) {
  await captureInventory({...options,emptyOnly:true});
}
function healthy(manifest) {
  requireCheck(manifest?.format==='kenkui-restore-inventory-v1'&&/^[a-f0-9]{64}$/.test(manifest.fullDigest),'Invalid restore inventory');
  requireCheck(inventoryDigest(manifest)===manifest.fullDigest,'Restore inventory digest mismatch');
  requireCheck(Object.keys(manifest.tables??{}).length>0&&Array.isArray(manifest.migrationIds),'Incomplete restore inventory');
  const c=manifest.creditInvariants;
  requireCheck(c&&['orphanReferences','duplicatePayments','invalidLots','crossOwnerAuthorizations','invalidAllocations','invalidLotAllocations'].every(k=>c[k]===0)&&
    Array.isArray(c.accounts)&&c.accounts.every(a=>a.availableCredits===a.lotAvailableCredits),'Credit/owner/payment invariant failed');
  requireCheck(manifest.r2?.missingObjects===0&&manifest.r2?.orphanReferences===0,'R2 reference invariant failed');
  requireCheck(manifest.managedAuth!=='required'||Object.keys(manifest.tables).some(k=>k.startsWith('neon_auth.')),'Managed auth inventory missing');
}
/** Read-only comparison. Never restores, migrates, drops or mutates either database. */
export function verifyRestore(source,target,review={mode:'copy'}) {
  healthy(source);healthy(target);
  requireCheck(source.environment===target.environment,'Restore environment mismatch');
  requireCheck(source.managedAuth===target.managedAuth||(review.mode==='migrated'&&review.managedAuthAdded===true&&source.managedAuth==='absent-reviewed-source'&&target.managedAuth==='required'),'Managed auth scope mismatch');
  if(review.mode==='copy') {
    for(const key of ['migrationIds','tables','sequences','creditInvariants','r2','fullDigest'])requireCheck(isDeepStrictEqual(source[key],target[key]),'Source-copy migration/table/digest mismatch');
    return {status:'verified-copy',tables:Object.keys(target.tables).length,fullDigest:target.fullDigest};
  }
  requireCheck(review.mode==='migrated'&&/^[a-f0-9]{64}$/.test(review.reviewedTargetDigest)&&review.reviewedTargetDigest===target.fullDigest,'Full target attestation required after DDL');
  requireCheck(Array.isArray(review.migrationIds)&&new Set(review.migrationIds).size===review.migrationIds.length&&
    isDeepStrictEqual([...source.migrationIds,...review.migrationIds].sort(),target.migrationIds),'Unknown migration delta');
  for(const [name,table] of Object.entries(source.tables)) {
    if(name==='public.schema_migrations')continue;
    requireCheck(isDeepStrictEqual(target.sourceProjection?.[name],{count:table.count,sha256:table.sha256}),'Original source rows not preserved after DDL');
  }
  requireCheck(isDeepStrictEqual(source.sequences,target.sequences),'Original sequence state changed after DDL');
  requireCheck(isDeepStrictEqual(source.creditInvariants,target.creditInvariants)&&isDeepStrictEqual(source.r2,target.r2),'Original financial/R2 facts not preserved');
  return {status:'verified-migration',tables:Object.keys(target.tables).length,fullDigest:target.fullDigest,restoredSnapshotDigest:target.restoredSnapshotDigest};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const [action,first,second,third,...extra]=process.argv.slice(2);
    if(extra.length)throw Error();
    if(action==='assert-empty'&&first&&!second) {
      await assertEmptyTarget({...await readPrivateJson(first),databaseUrl:process.env.KENKUI_INVENTORY_DATABASE_URL});
      console.log('Empty target verified; no database changes made.');
    } else if(action==='compare'&&first&&second) {
      console.log(JSON.stringify(verifyRestore(await readPrivateJson(first),await readPrivateJson(second),third?await readPrivateJson(third):{mode:'copy'})));
    } else throw Error();
  } catch {console.error('Restore verification failed; keep writers frozen and review private evidence.');process.exitCode=1;}
}
