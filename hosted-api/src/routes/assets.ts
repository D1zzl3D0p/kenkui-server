import { createHash, randomUUID } from 'node:crypto';
import type { Hono } from 'hono';
import { ApiError, type ApiEnv, type ApiConfig } from '../types.js';
import type { AssetResponse, ModalBridge } from '../compute/modal.js';
import type { AssetStore } from '../db/assets.js';
import type { ObjectStore } from '../storage/r2.js';

export interface AssetServices {assets:AssetStore;objects:ObjectStore;bridge:Pick<ModalBridge,'inspect'|'cover'|'replaceCover'>}
const fail=(status:413|415|422,message:string)=>new ApiError(status,'http_error',message);
export async function readBoundedBody(request:Request,max:number,limitMessage:string):Promise<Buffer> {
  const declared=request.headers.get('content-length');
  if(declared&&(!/^\d+$/.test(declared)||!Number.isSafeInteger(Number(declared))))throw fail(422,'Invalid content length.');
  if(declared&&Number(declared)>max)throw fail(413,limitMessage);
  const reader=request.body?.getReader();
  const chunks:Uint8Array[]=[];let size=0;
  try {
    if(reader)while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>max){await reader.cancel();throw fail(413,limitMessage);}chunks.push(value);}
  }catch(error){if(error instanceof ApiError)throw error;throw fail(422,'Incomplete upload.');}
  finally{reader?.releaseLock();}
  if(declared&&size!==Number(declared))throw fail(422,'Incomplete upload.');
  return Buffer.concat(chunks,size);
}
function imageType(bytes:Uint8Array):'image/png'|'image/jpeg'|undefined {
  if(bytes.length<32)return undefined;
  if(Buffer.from(bytes.subarray(0,8)).equals(Buffer.from('89504e470d0a1a0a','hex')))return 'image/png';
  if(bytes[0]===0xff&&bytes[1]===0xd8&&bytes[2]===0xff)return 'image/jpeg';
  return undefined;
}
export function registerAssetRoutes(app:Hono<ApiEnv>,config:ApiConfig,services:()=>AssetServices,newId:()=>string=randomUUID):void {
  app.post('/v1/assets',async c=>{
    if((c.req.header('content-type')??'').split(';',1)[0]!=='application/epub+zip')throw fail(415,'expected application/epub+zip');
    const bytes=await readBoundedBody(c.req.raw,config.catalog.capabilities.maxUploadBytes,'source exceeds upload limit');
    if(!bytes.length)throw fail(422,'empty asset');
    // Baseline accepts opaque EPUB-labelled bytes at upload; inspection validates EPUB.
    const asset:AssetResponse={id:newId(),format:'epub',sha256:createHash('sha256').update(bytes).digest('hex')};
    const owner=c.get('principal').userId;
    const selected=services();
    await selected.assets.beginUpload(asset,owner);
    try{await selected.objects.putSource(asset.id,bytes,asset.sha256);}
    catch{throw new ApiError(503,'storage_unavailable','Upload storage is temporarily unavailable.');}
    // Failures leave durable intent tracking, including lost PUT acknowledgements.
    const stored=await selected.assets.completeUpload(asset.id,owner);
    return c.json(stored,201);
  });
  app.get('/v1/assets/:id/book',async c=>{
    const selected=services(),owner=c.get('principal').userId,id=c.req.param('id');
    await selected.assets.requireOwned(id,owner);
    return c.json(await selected.bridge.inspect(owner,id));
  });
  app.get('/v1/assets/:id/cover',async c=>{
    const selected=services(),owner=c.get('principal').userId,id=c.req.param('id');
    await selected.assets.requireOwned(id,owner);
    const cover=await selected.bridge.cover(owner,id);
    return new Response(Buffer.from(cover.coverBase64,'base64'),{headers:{'Content-Type':cover.mediaType,'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
  });
  app.post('/v1/assets/:id/cover',async c=>{
    const selected=services(),owner=c.get('principal').userId,id=c.req.param('id');
    await selected.assets.requireOwned(id,owner);
    const type=(c.req.header('content-type')??'').split(';',1)[0];
    if(type!=='image/png'&&type!=='image/jpeg')throw fail(415,'Choose a PNG or JPEG cover.');
    const bytes=await readBoundedBody(c.req.raw,config.catalog.capabilities.covers.maxUploadBytes,'Cover exceeds 8 MB.');
    if(imageType(bytes)!==type)throw fail(422,'The cover or EPUB is invalid.');
    return c.json(await selected.bridge.replaceCover(owner,id,bytes,type),201);
  });
}
