import { createHash } from 'node:crypto';
import { GetObjectCommand, PutObjectCommand, DeleteObjectCommand, S3Client } from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { ApiError } from '../types.js';
import { requireUuid } from '../compute/modal.js';

export interface ObjectStore {
  putSource(id:string,bytes:Uint8Array,sha256:string):Promise<void>;
  deleteSource(id:string):Promise<void>;
  artifactUrl(id:string,filename:string):Promise<string>;
  streamArtifact(id:string,range?:string):Promise<Response>;
}
export interface R2Config {endpoint:string;bucket:string;keySalt:string;accessKeyId:string;secretAccessKey:string}
export function objectKey(salt:string,kind:'source'|'artifact',id:string):string {
  const identifiers=id.split('.');
  if(identifiers.length<1||identifiers.length>(kind==='artifact'?2:1))throw new ApiError(422,'http_error','Invalid resource identifier.');
  for(const identifier of identifiers)requireUuid(identifier);
  return `${kind}s/${createHash('sha256').update(`${salt}:${kind}:${id}`).digest('hex')}`;
}
export function artifactFilename(title:string|null|undefined,jobId:string):string {
  // Python indexes Unicode code points; do not split astral characters at 180.
  const sanitized=Array.from((title||jobId).replace(/[\x00-\x1f\x7f\\/:*?"<>|]/g,'_')).slice(0,180).join('').replace(/^[ .]+|[ .]+$/g,'');
  return (sanitized||jobId)+'.m4b';
}
export class R2ObjectStore implements ObjectStore {
  private readonly client:S3Client;
  constructor(private readonly config:R2Config,client?:S3Client) {
    const endpoint=new URL(config.endpoint);
    if(endpoint.protocol!=='https:'||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||endpoint.pathname!=='/'||
      !config.bucket||!config.accessKeyId||!config.secretAccessKey)throw new Error('Invalid private R2 configuration');
    this.client=client??new S3Client({endpoint:endpoint.origin,region:'auto',forcePathStyle:true,maxAttempts:2,
      credentials:{accessKeyId:config.accessKeyId,secretAccessKey:config.secretAccessKey},
      requestChecksumCalculation:'WHEN_REQUIRED',responseChecksumValidation:'WHEN_REQUIRED'});
  }
  async putSource(id:string,bytes:Uint8Array,sha256:string):Promise<void> {
    const key=objectKey(this.config.keySalt,'source',id);
    if(createHash('sha256').update(bytes).digest('hex')!==sha256)throw new ApiError(422,'http_error','Source digest mismatch.');
    await this.client.send(new PutObjectCommand({Bucket:this.config.bucket,Key:key,Body:bytes,ContentType:'application/epub+zip',ContentLength:bytes.byteLength}),{abortSignal:AbortSignal.timeout(120_000)});
  }
  async deleteSource(id:string):Promise<void> {
    await this.client.send(new DeleteObjectCommand({Bucket:this.config.bucket,Key:objectKey(this.config.keySalt,'source',id)}),{abortSignal:AbortSignal.timeout(30_000)});
  }
  async artifactUrl(id:string,filename:string):Promise<string> {
    if(/[\r\n"\\]/.test(filename)||!filename.endsWith('.m4b'))throw new Error('Invalid artifact filename');
    return getSignedUrl(this.client,new GetObjectCommand({Bucket:this.config.bucket,Key:objectKey(this.config.keySalt,'artifact',id),
      ResponseContentDisposition:`attachment; filename="${filename}"`}),{expiresIn:300});
  }
  async streamArtifact(id:string,range?:string):Promise<Response> {
    const key=objectKey(this.config.keySalt,'artifact',id);
    if(range&&!/^bytes=(?:\d+-\d*|-\d+)$/.test(range))throw new ApiError(416,'http_error','Requested Range Not Satisfiable');
    const controller=new AbortController();
    let output;
    try{output=await this.client.send(new GetObjectCommand({Bucket:this.config.bucket,Key:key,...(range?{Range:range}:{})}),{abortSignal:controller.signal});}
    catch(error){
      if(error instanceof Error&&['NoSuchKey','NotFound'].includes(error.name))throw new ApiError(404,'not_found','Not found');
      if(error instanceof Error&&error.name==='InvalidRange')throw new ApiError(416,'http_error','Requested Range Not Satisfiable');
      throw error;
    }
    if(!output.Body)throw new ApiError(404,'not_found','Not found');
    const body=output.Body.transformToWebStream();
    const reader=body.getReader();
    // Pull one provider chunk per consumer pull; cancellation closes the R2 socket.
    const stream=new ReadableStream<Uint8Array>({
      async pull(c){try{const {done,value}=await reader.read();if(done){c.close();reader.releaseLock();}else c.enqueue(value);}catch(error){controller.abort();c.error(error);}},
      async cancel(reason){controller.abort();await reader.cancel(reason);reader.releaseLock();},
    },{highWaterMark:0});
    const headers=new Headers({'Content-Type':'audio/mp4','Accept-Ranges':'bytes','Cache-Control':'no-store'});
    if(output.ContentLength!==undefined)headers.set('Content-Length',String(output.ContentLength));
    if(output.ContentRange)headers.set('Content-Range',output.ContentRange);
    return new Response(stream,{status:output.ContentRange?206:200,headers});
  }
}
