import type { Pool, PoolClient } from 'pg';
import { ApiError } from '../types.js';
import { requireUuid, UUID_PATTERN, type AssetResponse } from '../compute/modal.js';

interface AssetRow {id:string;format:string;sha256:string;owner_id:string;deleted_at:Date|null}
interface UploadRow {source_id:string;owner_id:string;sha256:string|null;eligible:boolean;cover_operation_id:string|null;cover_generation:number|null}
const conflict=()=>new ApiError(409,'http_error','Upload expired or conflicts with a stored source.');
function publicAsset(row:AssetRow):AssetResponse{return {id:row.id,format:row.format,sha256:row.sha256};}
function ownerCheck(actual:string,expected:string):void {if(actual!==expected)throw new ApiError(403,'http_error','resource is not owned by this user');}

/** Lazy SQL only. No migrations or object operations inside these transactions. */
export class AssetStore {
  constructor(private readonly getPool:()=>Pool) {}
  async requireOwned(id:string,owner:string):Promise<AssetResponse> {
    if(!UUID_PATTERN.test(id))throw new ApiError(404,'not_found','Not found');
    requireUuid(owner);
    const row=(await this.getPool().query<AssetRow>('SELECT id,format,sha256,owner_id,deleted_at FROM assets WHERE id=$1',[id])).rows[0];
    if(!row)throw new ApiError(404,'not_found','Not found');
    ownerCheck(row.owner_id,owner);
    if(row.deleted_at)throw new ApiError(404,'not_found','Not found');
    if(row.format!=='epub')throw new ApiError(415,'http_error','expected application/epub+zip');
    return publicAsset(row);
  }
  private async transaction<T>(id:string,operation:(client:PoolClient)=>Promise<T>):Promise<T> {
    const client=await this.getPool().connect();
    try {
      await client.query('BEGIN');
      // Shared with Python reserve/finalize/retention; always acquire before row locks.
      await client.query('SELECT pg_advisory_xact_lock(hashtextextended($1,0))',[id]);
      const result=await operation(client);await client.query('COMMIT');return result;
    }catch(error){await client.query('ROLLBACK');throw error;}finally{client.release();}
  }
  async beginUpload(asset:AssetResponse,owner:string):Promise<AssetResponse|null> {
    requireUuid(asset.id);requireUuid(owner);
    if(asset.format!=='epub'||!/^[0-9a-f]{64}$/.test(asset.sha256))throw conflict();
    return this.transaction(asset.id,async client=>{
      const completed=(await client.query<AssetRow>('SELECT id,format,sha256,owner_id,deleted_at FROM assets WHERE id=$1',[asset.id])).rows[0];
      if(completed){ownerCheck(completed.owner_id,owner);if(completed.deleted_at||completed.sha256!==asset.sha256||completed.format!==asset.format)throw conflict();return publicAsset(completed);}
      await client.query(`INSERT INTO source_upload_intents(source_id,owner_id,sha256) VALUES($1,$2,$3) ON CONFLICT(source_id) DO NOTHING`,[asset.id,owner,asset.sha256]);
      const intent=(await client.query<UploadRow>(`SELECT source_id,owner_id,sha256,cover_operation_id,cover_generation,NOT cleanup_started AND clock_timestamp()<expires_at AS eligible FROM source_upload_intents WHERE source_id=$1 FOR UPDATE`,[asset.id])).rows[0];
      if(!intent)throw conflict();ownerCheck(intent.owner_id,owner);
      if(!intent.eligible)throw conflict();
      if(intent.sha256===null&&intent.cover_operation_id!==null) {
        await client.query('UPDATE source_upload_intents SET sha256=$2 WHERE source_id=$1',[asset.id,asset.sha256]);
      }else if(intent.sha256!==asset.sha256)throw conflict();
      return null;
    });
  }
  async completeUpload(id:string,owner:string):Promise<AssetResponse> {
    requireUuid(id);requireUuid(owner);
    return this.transaction(id,async client=>{
      const completed=(await client.query<AssetRow>('SELECT id,format,sha256,owner_id,deleted_at FROM assets WHERE id=$1',[id])).rows[0];
      if(completed){ownerCheck(completed.owner_id,owner);if(completed.deleted_at)throw conflict();return publicAsset(completed);}
      const intent=(await client.query<UploadRow>(`SELECT source_id,owner_id,sha256,cover_operation_id,cover_generation,NOT cleanup_started AND clock_timestamp()<expires_at AS eligible FROM source_upload_intents WHERE source_id=$1 FOR UPDATE`,[id])).rows[0];
      if(!intent)throw conflict();ownerCheck(intent.owner_id,owner);if(!intent.eligible||!intent.sha256)throw conflict();
      await client.query(`INSERT INTO assets(id,path,sha256,format,owner_id,cover_operation_id,cover_generation) VALUES($1,$1,$2,'epub',$3,$4,$5)`,[id,intent.sha256,owner,intent.cover_operation_id,intent.cover_generation]);
      await client.query('DELETE FROM source_upload_intents WHERE source_id=$1',[id]);
      return {id,format:'epub',sha256:intent.sha256};
    });
  }
}
