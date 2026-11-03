import { randomUUID } from 'node:crypto';
import type { Hono } from 'hono';
import { creditsForCharacters,jobSpec } from '../billing/job-spec.js';
import type { ModalBridge } from '../compute/modal.js';
import type { AssetStore } from '../db/assets.js';
import type { BillingStore } from '../db/billing.js';
import { submitJob,type AdmissionResult,type JobStore,type RecoverySignals } from '../db/jobs.js';
import { artifactFilename,type ObjectStore } from '../storage/r2.js';
import { ApiError,type ApiConfig,type ApiEnv } from '../types.js';
import { getOnly } from './get-only.js';
import { bridgeLimits,jobRequest,rawInteger,selectedCastingLimits } from './job-request.js';

export interface JobServices {
  jobs:Pick<JobStore,'admitJob'|'getOwned'|'listOwned'|'cancelOwned'|'artifactForJob'>;
  assets:Pick<AssetStore,'requireOwned'>;billing:Pick<BillingStore,'accountForOwner'>;
  bridge:Pick<ModalBridge,'preflight'|'dispatch'>;signals:RecoverySignals;objects:Pick<ObjectStore,'artifactUrl'>;
}
type Snapshot=Pick<AdmissionResult,'jobId'|'spec'|'status'|'progressJson'>&{failure?:Record<string,string>|null};
export function jobResponse(job:Snapshot,includeFailure=false):Record<string,unknown> {
  const spec=job.spec,c=spec.casting;
  const progress=JSON.parse(job.progressJson,(_key:string,value:unknown,context?:{source?:string})=>typeof value==='number'&&context?.source&&/^-?\d+$/.test(context.source)?rawInteger(context.source):value);
  return {sourceCover:spec.output.source_cover,sourceId:spec.source_id,...(spec.output.title!==null?{title:spec.output.title}:{}),...(spec.output.author!==null?{author:spec.output.author}:{}),
    narratorVoiceId:c.kind==='single'?c.voice_id:c.narrator_voice_id,castingMode:c.kind,
    ...(includeFailure&&job.failure?{failure:job.failure}:{}),id:job.jobId,status:job.status,progress:{stage:progress.stage,completed:progress.completed,total:progress.total}};
}
export function registerJobRoutes(app:Hono<ApiEnv>,config:ApiConfig,services:()=>JobServices,maxSpeechCharacters=10000000):void {
  if(!Number.isSafeInteger(maxSpeechCharacters)||maxSpeechCharacters<1)throw Error('Invalid speech size limit');
  const preflight=async(raw:string,contentType:string|undefined,owner:string)=>{
    const job=jobRequest(raw,contentType);
    selectedCastingLimits(job);
    const spec=jobSpec(job,config.catalog.capabilities.casting.models);
    const selected=services();
    await selected.assets.requireOwned(spec.source_id,owner);
    const c=spec.casting;
    const executable={...job,sourceId:spec.source_id,chapters:spec.chapters,casting:c.kind==='single'?{voiceId:c.voice_id}:{
      narratorVoiceId:c.narrator_voice_id,unknownVoiceId:c.unknown_voice_id,modelId:c.model_id,method:c.method,cast:Object.fromEntries(c.cast as [string,string][]),
    }};
    bridgeLimits(executable,owner,config.environment);
    const voices=c.kind==='single'?[c.voice_id]:[c.narrator_voice_id,c.unknown_voice_id,...c.cast.map(([,voice])=>voice!)];
    const available=new Set(config.catalog.voices.map(v=>v.id));
    if(voices.some(v=>!available.has(v)))throw new ApiError(422,'http_error','voice is unavailable');
    // Python reconstructs the pipeline with these exact options and selection.
    const result=await selected.bridge.preflight(owner,executable),characters=result.normalizedCharacters;
    if(characters<=0)throw new ApiError(422,'http_error','empty_speech');
    if(characters>maxSpeechCharacters)throw new ApiError(422,'http_error','job_size_limit');
    const account=await selected.billing.accountForOwner(owner),credits=creditsForCharacters(characters,c.kind==='characters');
    return {selected,spec,characters,account,credits};
  };
  const methodNotAllowed=()=>{throw new ApiError(405,'method_not_allowed','Method not allowed');};
  app.post('/v1/jobs/preflight',async c=>{
    const x=await preflight(await c.req.text(),c.req.header('Content-Type'),c.get('principal').userId);
    return c.json({sourceId:x.spec.source_id,normalizedCharacters:x.characters,valid:BigInt(x.account.availableCredits)>=x.credits,estimatedCredits:rawInteger(x.credits),availableCredits:rawInteger(x.account.availableCredits)});
  });
  // Static POST-only resources must precede the dynamic GET /:id in Hono.
  app.all('/v1/jobs/preflight',methodNotAllowed);
  app.post('/v1/jobs',async c=>{
    const owner=c.get('principal').userId,x=await preflight(await c.req.text(),c.req.header('Content-Type'),owner);
    const admitted=await submitJob(x.selected.jobs,x.selected.signals,id=>x.selected.bridge.dispatch(id),{
      jobId:randomUUID(),dispatchId:randomUUID(),ownerId:owner,accountId:x.account.id,spec:x.spec,credits:x.credits,idempotencyKey:c.req.header('Idempotency-Key')??null,
    });
    return c.json(jobResponse(admitted),202);
  });
  app.get('/v1/jobs',getOnly,async c=>c.json({items:(await services().jobs.listOwned(c.get('principal').userId)).map(j=>jobResponse(j))}));
  app.get('/v1/jobs/:id',getOnly,async c=>c.json(jobResponse(await services().jobs.getOwned(c.req.param('id'),c.get('principal').userId),true)));
  app.post('/v1/jobs/:id/cancel',async c=>c.json(jobResponse(await services().jobs.cancelOwned(c.req.param('id'),c.get('principal').userId))));
  for(const suffix of ['artifact','artifact-url'])app.get('/v1/jobs/:id/'+suffix,getOnly,async c=>{
    const selected=services(),job=await selected.jobs.getOwned(c.req.param('id')!,c.get('principal').userId);
    if(job.status!=='succeeded')throw new ApiError(409,'http_error','artifact is not available');
    const path=await selected.jobs.artifactForJob(job.jobId);
    const url=await selected.objects.artifactUrl(path,artifactFilename(job.spec.output.title,job.jobId));
    if(suffix==='artifact')return new Response(null,{status:303,headers:{Location:url,'Cache-Control':'no-store'}});
    return c.json({url},200,{'Cache-Control':'no-store'});
  });
  for(const path of ['/v1/jobs','/v1/jobs/:id','/v1/jobs/:id/cancel','/v1/jobs/:id/artifact','/v1/jobs/:id/artifact-url'])app.all(path,methodNotAllowed);
}
