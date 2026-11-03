import { createHash } from 'node:crypto';
import { Readable } from 'node:stream';
import { S3Client } from '@aws-sdk/client-s3';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { registerAssetRoutes, type AssetServices } from '../src/routes/assets.js';
import { R2ObjectStore, objectKey, artifactFilename } from '../src/storage/r2.js';

const owner='11111111-1111-4111-8111-111111111111';
const id='22222222-2222-4222-8222-222222222222';
const asset={id,format:'epub',sha256:'a'.repeat(64)};
const principal={userId:owner,issuer:'https://auth.synthetic.invalid',subject:'synthetic'};
function setup(options:{foreign?:boolean;maxUpload?:number}={}) {
  const config=loadConfig({KENKUI_ENV:'staging'});
  if(options.maxUpload) config.catalog.capabilities.maxUploadBytes=options.maxUpload;
  const store={requireOwned:vi.fn(async()=>{if(options.foreign) {
    const {ApiError}=await import('../src/types.js');throw new ApiError(403,'http_error','resource is not owned by this user');
  }return asset;}),beginUpload:vi.fn(async():Promise<typeof asset|null>=>null),completeUpload:vi.fn(async()=>asset)};
  const objects={putSource:vi.fn(async()=>{}),deleteSource:vi.fn(async()=>{}),artifactUrl:vi.fn(async()=>''),streamArtifact:vi.fn()};
  const bridge={inspect:vi.fn(async()=>({sourceId:id,title:'Tiny',author:'Ada',chapters:[]})),
    cover:vi.fn(async()=>({coverBase64:'iVBORw0KGgo=',mediaType:'image/png'})),
    replaceCover:vi.fn(async()=>asset)};
  const app=createApi({config,getPool:()=>{throw new Error('unused');},auth:{verify:async()=>principal}});
  registerAssetRoutes(app,config,()=>({assets:store,objects,bridge} as unknown as AssetServices),()=>id);
  const request=(path:string,init:RequestInit={})=>app.request(path,{...init,headers:{authorization:'Bearer synthetic',...init.headers}});
  return {request,store,objects,bridge};
}
describe('owned asset public routes',()=>{
  it('stores bounded source bytes with SHA256 and server owner before returning a private ID',async()=>{
    const {request,store,objects}=setup();
    const body=Buffer.from('not yet parsed EPUB');
    const response=await request('/v1/assets',{method:'POST',headers:{'content-type':'application/epub+zip'},body});
    expect(response.status).toBe(201);
    expect(store.beginUpload).toHaveBeenCalledWith({...asset,sha256:createHash('sha256').update(body).digest('hex')},owner);
    expect(objects.putSource).toHaveBeenCalledWith(id,body,createHash('sha256').update(body).digest('hex'));
    expect(store.completeUpload).toHaveBeenCalledWith(id,owner);
    expect(store.beginUpload.mock.invocationCallOrder[0]).toBeLessThan(objects.putSource.mock.invocationCallOrder[0]!);
  });
  it('returns an already completed upload without another object write or finalization',async()=>{
    const {request,store,objects}=setup();
    const body=Buffer.from('completed EPUB bytes');
    const completed={...asset,sha256:createHash('sha256').update(body).digest('hex')};
    store.beginUpload.mockResolvedValue(completed);
    const response=await request('/v1/assets',{method:'POST',headers:{'content-type':'application/epub+zip'},body});
    expect(response.status).toBe(201);expect(await response.json()).toEqual(completed);
    expect(objects.putSource).not.toHaveBeenCalled();expect(store.completeUpload).not.toHaveBeenCalled();
  });
  it.each([
    ['application/pdf','%PDF',415],['application/octet-stream','bytes',415],['application/epub+zip','',422],
    ['application/epub+zip','too long',413],
  ])('validates format/empty/limits before storage (%s)',async(type,body,status)=>{
    const {request,store,objects}=setup({maxUpload:4});
    expect((await request('/v1/assets',{method:'POST',headers:{'content-type':type},body})).status).toBe(status);
    expect(store.beginUpload).not.toHaveBeenCalled();expect(objects.putSource).not.toHaveBeenCalled();
  });
  it('rejects truncated content-length and broken streams without uploading',async()=>{
    const {request,objects}=setup();
    expect((await request('/v1/assets',{method:'POST',headers:{'content-type':'application/epub+zip','content-length':'100'},body:'short'})).status).toBe(422);
    const body=new ReadableStream<Uint8Array>({start(c){c.enqueue(new Uint8Array([1]));c.error(new Error('disconnect'));}});
    const init={method:'POST',headers:{'content-type':'application/epub+zip'},body,duplex:'half'} as RequestInit;
    expect((await request('/v1/assets',init)).status).toBe(422);
    expect(objects.putSource).not.toHaveBeenCalled();
  });
  it.each(['book','cover'])('authorizes %s before compute or object access',async operation=>{
    const {request,objects,bridge}=setup({foreign:true});
    expect((await request(`/v1/assets/${id}/${operation}`)).status).toBe(403);
    expect(bridge.inspect).not.toHaveBeenCalled();expect(bridge.cover).not.toHaveBeenCalled();expect(objects.putSource).not.toHaveBeenCalled();
    expect((await request(`/v1/assets/${id}/cover`,{method:'POST',headers:{'content-type':'image/png'},body:'bad'})).status).toBe(403);
    expect(bridge.replaceCover).not.toHaveBeenCalled();
  });
  it('replaces cover immutably, validates image type, and keeps cover cache/MIME headers',async()=>{
    const {request,bridge,objects}=setup();
    const cover=Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),Buffer.alloc(24)]);
    const response=await request(`/v1/assets/${id}/cover`,{method:'POST',headers:{'content-type':'image/png'},body:cover});
    expect(response.status).toBe(201);
    expect(bridge.replaceCover).toHaveBeenCalledWith(owner,id,cover,'image/png');expect(objects.putSource).not.toHaveBeenCalled();
    expect((await request(`/v1/assets/${id}/cover`,{method:'POST',headers:{'content-type':'image/jpeg'},body:cover})).status).toBe(422);
    const fetched=await request(`/v1/assets/${id}/cover`);
    expect(fetched.headers.get('content-type')).toBe('image/png');
    expect(fetched.headers.get('cache-control')).toBe('private, no-store');expect(fetched.headers.get('x-content-type-options')).toBe('nosniff');
  });
  it('rejects empty and short cover payloads before compute',async()=>{
    const {request,bridge}=setup();
    for(const body of [Buffer.alloc(0),Buffer.from('89504e470d0a1a0a','hex')]) {
      expect((await request(`/v1/assets/${id}/cover`,{method:'POST',headers:{'content-type':'image/png'},body})).status).toBe(422);
    }
    expect(bridge.replaceCover).not.toHaveBeenCalled();
  });
  it('retains tracking after an ambiguous PUT and never publishes an incomplete asset',async()=>{
    const {request,store,objects}=setup();objects.putSource.mockRejectedValue(new Error('lost acknowledgement'));
    expect((await request('/v1/assets',{method:'POST',headers:{'content-type':'application/epub+zip'},body:'bytes'})).status).toBe(503);
    expect(store.beginUpload).toHaveBeenCalledOnce();expect(store.completeUpload).not.toHaveBeenCalled();
  });
});

describe('private R2 transfers',()=>{
  it('matches Python salted keys, refuses caller paths and detects digest mismatches before provider access',async()=>{
    expect(objectKey('salt','source',id)).toBe('sources/'+createHash('sha256').update(`salt:source:${id}`).digest('hex'));
    expect(()=>objectKey('salt','artifact','../../secret')).toThrow();
    const storage=new R2ObjectStore({endpoint:'https://synthetic.r2.cloudflarestorage.com',bucket:'private',keySalt:'salt',accessKeyId:'synthetic',secretAccessKey:'synthetic-secret'});
    await expect(storage.putSource(id,Buffer.from('bytes'),'0'.repeat(64))).rejects.toMatchObject({status:422});
  });
  it('matches Python worker attempt identifiers and streams Range responses with backpressure/cancellation',async()=>{
    const identifier=id+'.33333333-3333-4333-8333-333333333333';
    expect(objectKey('salt','artifact',identifier)).toBe('artifacts/'+createHash('sha256').update(`salt:artifact:${identifier}`).digest('hex'));
    let produced=0;const body=new Readable({highWaterMark:65536,read(){produced++;this.push(Buffer.alloc(65536));}});
    let actualRange:string|undefined;
    const client=new S3Client({region:'auto',endpoint:'https://synthetic.r2.cloudflarestorage.com',credentials:{accessKeyId:'synthetic',secretAccessKey:'synthetic-secret'},
      responseChecksumValidation:'WHEN_REQUIRED',requestChecksumCalculation:'WHEN_REQUIRED',requestHandler:{async handle(request:{headers:Record<string,string>}){actualRange=request.headers.range;
        return {response:{statusCode:206,headers:{'content-type':'audio/mp4','content-length':'65536','content-range':'bytes 0-65535/999999999'},body}};}}});
    const store=new R2ObjectStore({endpoint:'https://synthetic.r2.cloudflarestorage.com',bucket:'private',keySalt:'salt',accessKeyId:'synthetic',secretAccessKey:'synthetic-secret'},client);
    const response=await store.streamArtifact(identifier,'bytes=0-65535');
    expect(response.status).toBe(206);expect(actualRange).toBe('bytes=0-65535');
    expect(response.headers.get('content-range')).toBe('bytes 0-65535/999999999');expect(response.headers.get('content-type')).toBe('audio/mp4');
    const reader=response.body!.getReader();expect((await reader.read()).value).toHaveLength(65536);
    expect(produced).toBeLessThan(10);await reader.cancel();expect(body.destroyed).toBe(true);client.destroy();
  });
  it('preserves safe artifact filenames and private 300-second signed transfer semantics',async()=>{
    expect(artifactFilename(' A/B: test? ',id)).toBe('A_B_ test_.m4b');
    expect(artifactFilename('日本語',id)).toBe('日本語.m4b');
    const store=new R2ObjectStore({endpoint:'https://synthetic.r2.cloudflarestorage.com',bucket:'private',keySalt:'salt',accessKeyId:'synthetic',secretAccessKey:'synthetic-secret'});
    const url=new URL(await store.artifactUrl(id,artifactFilename('Title',id)));
    expect(url.protocol).toBe('https:');expect(url.pathname).toContain(objectKey('salt','artifact',id));
    expect(url.searchParams.get('X-Amz-Expires')).toBe('300');
    expect(url.searchParams.get('response-content-disposition')).toBe('attachment; filename="Title.m4b"');
  });
});
