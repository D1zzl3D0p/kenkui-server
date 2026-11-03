import { ApiError } from '../types.js';

export const UUID_PATTERN=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function requireUuid(value:string):void {
  if(!UUID_PATTERN.test(value)) throw new ApiError(422,'http_error','Invalid resource identifier.');
}
export interface AssetResponse {id:string;format:string;sha256:string}
export interface BookResponse {sourceId:string;title:string;author:string;chapters:{id:string;title:string;speechCharacters:number|null}[]}
export interface PreflightResponse {sourceId:string;normalizedCharacters:number;valid:boolean;estimatedCredits:number|null;availableCredits:number|null}
export interface CoverResponse {coverBase64:string;mediaType:'image/png'|'image/jpeg'}
export interface StrictJob {
  sourceId:string;chapters:string[];
  casting:{voiceId?:string|null;narratorVoiceId?:string|null;unknownVoiceId?:string|null;modelId?:string|null;method?:string;cast?:Record<string,string>};
  tts?:{normalizeText?:boolean;chapterPauses?:boolean;prepareNumbers?:boolean;pronunciationCorrections?:boolean;stutterHandling?:boolean;chapterPauseMs?:number|null;headingBeforePauseMs?:number;headingAfterPauseMs?:number;paragraphPauseMs?:number;linePauseMs?:number;scenePauseMs?:number};
  output?:{format?:string;title?:string|null;author?:string|null;sourceCover?:boolean};
}
export interface ModalConfig {url:string;environment:'staging'|'production';key:string;secret:string;bridgeSecret:string;timeoutMs?:number}
type Transport=(input:string,init:RequestInit)=>Promise<Response>;
const unavailable=()=>new ApiError(503,'compute_unavailable','Document processing is temporarily unavailable.',{}, {'Retry-After':'5'});
const invalid=()=>new ApiError(502,'compute_response_invalid','Document processing returned an invalid response.');
const record=(x:unknown):x is Record<string,unknown>=>typeof x==='object'&&x!==null&&!Array.isArray(x);
const asset=(x:unknown):x is AssetResponse=>record(x)&&typeof x.id==='string'&&UUID_PATTERN.test(x.id)&&x.format==='epub'&&typeof x.sha256==='string'&&/^[0-9a-f]{64}$/.test(x.sha256);

/** Fixed server-owned Modal endpoint. No redirects, implicit retries or user URLs. */
export class ModalBridge {
  private readonly base:string;
  private readonly timeout:number;
  constructor(private readonly config:ModalConfig,private readonly transport:Transport=fetch) {
    const url=new URL(config.url);
    if(url.protocol!=='https:'||!url.hostname.endsWith('.modal.run')||url.username||url.password||url.port||url.search||url.hash||url.pathname!=='/'||
      !config.key||!config.secret||!config.bridgeSecret||!['staging','production'].includes(config.environment)) throw new Error('Invalid Modal bridge configuration');
    this.base=url.origin;
    this.timeout=config.timeoutMs??120_000;
    if(!Number.isSafeInteger(this.timeout)||this.timeout<=0||this.timeout>120_000) throw new Error('Invalid Modal deadline');
  }
  private async call(path:string,payload:Record<string,unknown>,limit=12_000_000):Promise<unknown> {
    const controller=new AbortController();
    let timer:ReturnType<typeof setTimeout>|undefined;
    const deadline=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(unavailable());},this.timeout);});
    const operation=async()=>{
      let response:Response;
      try {
        response=await this.transport(this.base+path,{method:'POST',redirect:'error',signal:controller.signal,
          headers:{'Content-Type':'application/json','Modal-Key':this.config.key,'Modal-Secret':this.config.secret,'X-Kenkui-Bridge-Secret':this.config.bridgeSecret},
          body:JSON.stringify({environment:this.config.environment,...payload})});
      }catch{throw unavailable();}
      if(!response.ok) {
        let detail:unknown;
        if(path==='/preflight'&&response.status===422){
          const reader=response.body?.getReader();const chunks:Uint8Array[]=[];let size=0;
          try{
            if(reader)while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>2048){await reader.cancel();break;}chunks.push(value);}
            if(size<=2048)detail=(JSON.parse(Buffer.concat(chunks,size).toString('utf8')) as {detail?:unknown}).detail;
          }catch{/* Untrusted response text never reaches a public error. */}finally{reader?.releaseLock();}
        }else await response.body?.cancel();
        if(response.status===422&&typeof detail==='string'&&['unknown chapter id','voice is unavailable','empty_speech','job_size_limit'].includes(detail))throw new ApiError(422,'http_error',detail);
        if(response.status===429) throw new ApiError(429,'compute_unavailable','Document processing is temporarily unavailable.',{}, {'Retry-After':'5'});
        if(response.status===404) throw new ApiError(404,'not_found','Not found');
        if(response.status===403) throw new ApiError(403,'http_error','resource is not owned by this user');
        if(response.status===409) throw new ApiError(409,'http_error',path==='/replace_cover'?'Cover processing conflicted. Try again.':"This book's chapter layout has changed since it was uploaded. Upload the book again and choose chapters from the updated list.");
        if(response.status===413) throw new ApiError(413,'http_error',path==='/replace_cover'?'The book with its new cover exceeds the upload limit.':'source exceeds upload limit');
        if(response.status===415) throw new ApiError(415,'http_error','expected application/epub+zip');
        if(response.status===422) throw new ApiError(422,'http_error',path==='/replace_cover'?'The cover or EPUB is invalid.':path==='/cover'?'The source cover cannot be read.':'The source or job is invalid.');
        throw unavailable();
      }
      const declared=response.headers.get('content-length');
      if(declared&&(!/^\d+$/.test(declared)||Number(declared)>limit)) {await response.body?.cancel();throw invalid();}
      const reader=response.body?.getReader();
      if(!reader) throw invalid();
      const chunks:Uint8Array[]=[];let size=0;
      try {
        while(true) {
          const {done,value}=await reader.read();if(done)break;
          size+=value.byteLength;if(size>limit){await reader.cancel();throw invalid();}chunks.push(value);
        }
        return JSON.parse(Buffer.concat(chunks,size).toString('utf8')) as unknown;
      }catch(error){if(error instanceof ApiError)throw error;throw invalid();}
      finally{reader.releaseLock();}
    };
    try{return await Promise.race([operation(),deadline]);}finally{if(timer)clearTimeout(timer);}
  }
  async inspect(ownerId:string,sourceId:string):Promise<BookResponse> {
    requireUuid(ownerId);requireUuid(sourceId);
    const x=await this.call('/inspect',{ownerId,sourceId});
    if(!record(x)||x.sourceId!==sourceId||typeof x.title!=='string'||typeof x.author!=='string'||!Array.isArray(x.chapters)||
      !x.chapters.every(c=>record(c)&&typeof c.id==='string'&&typeof c.title==='string'&&(c.speechCharacters===null||(Number.isSafeInteger(c.speechCharacters)&&Number(c.speechCharacters)>=0)))) throw invalid();
    return x as unknown as BookResponse;
  }
  async preflight(ownerId:string,job:StrictJob):Promise<PreflightResponse> {
    requireUuid(ownerId);requireUuid(job.sourceId);
    const x=await this.call('/preflight',{ownerId,job});
    if(!record(x)||x.sourceId!==job.sourceId||x.valid!==true||!Number.isSafeInteger(x.normalizedCharacters)||Number(x.normalizedCharacters)<0||x.estimatedCredits!==null||x.availableCredits!==null)throw invalid();
    return x as unknown as PreflightResponse;
  }
  async cover(ownerId:string,sourceId:string):Promise<CoverResponse> {
    requireUuid(ownerId);requireUuid(sourceId);
    const x=await this.call('/cover',{ownerId,sourceId});
    if(!record(x)||!['image/png','image/jpeg'].includes(String(x.mediaType))||typeof x.coverBase64!=='string'||
      x.coverBase64.length>11_184_812||!x.coverBase64)throw invalid();
    const bytes=Buffer.from(x.coverBase64,'base64');
    if(bytes.length>8_388_608||bytes.toString('base64')!==x.coverBase64)throw invalid();
    return x as unknown as CoverResponse;
  }
  async replaceCover(ownerId:string,sourceId:string,bytes:Uint8Array,mediaType:'image/png'|'image/jpeg'):Promise<AssetResponse> {
    requireUuid(ownerId);requireUuid(sourceId);
    const x=await this.call('/replace_cover',{ownerId,sourceId,coverBase64:Buffer.from(bytes).toString('base64'),mediaType});
    if(!asset(x))throw invalid();return x;
  }
  async dispatch(dispatchId:string):Promise<{accepted:boolean}> {
    requireUuid(dispatchId);
    const x=await this.call('/dispatch',{dispatchId});
    if(!record(x)||typeof x.accepted!=='boolean')throw invalid();return {accepted:x.accepted};
  }
}
