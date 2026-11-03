import { randomUUID } from 'node:crypto';
import { expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { jobSpec } from '../src/billing/job-spec.js';
import { ApiError } from '../src/types.js';
import { registerJobRoutes } from '../src/routes/jobs.js';

const owner=randomUUID(),source=randomUUID(),id=randomUUID(),dispatchId=randomUUID();
const config=loadConfig({KENKUI_ENV:'staging'}),voice=config.catalog.voices[0]!.id;
const headers={Authorization:'Bearer fixture','Content-Type':'application/json'};
const payload={sourceId:source,chapters:['one'],casting:{voiceId:voice}};
function setup(){
  const app=createApi({config,getPool(){throw Error('unexpected DB');},auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
  const row={jobId:id,dispatchId,spec:jobSpec(payload),status:'queued',version:0,progressJson:'{"stage":"queued","completed":0,"total":0}',replayed:false};
  const order:string[]=[];
  const jobs={admitJob:vi.fn(async(_input:unknown)=>{order.push('commit');return row;}),listOwned:vi.fn().mockResolvedValue([row]),getOwned:vi.fn().mockResolvedValue(row),cancelOwned:vi.fn().mockResolvedValue({...row,status:'cancelled'}),artifactForJob:vi.fn().mockResolvedValue(id+'.'+dispatchId)};
  const assets={requireOwned:vi.fn(async(_source:string,_owner:string)=>{order.push('owned');return {id:source,format:'epub',sha256:'a'.repeat(64)};})};
  const billing={accountForOwner:vi.fn(async()=>{order.push('account');return {id:randomUUID(),availableCredits:'9007199254740993'};})};
  const bridge={preflight:vi.fn(async(_owner:string,_job:unknown)=>{order.push('preflight');return {sourceId:source,normalizedCharacters:3969,valid:true,availableCredits:null,estimatedCredits:null};}),dispatch:vi.fn(async()=>{order.push('dispatch');return {accepted:true};})};
  const signals={arm:vi.fn(async()=>{order.push('arm');}),clear:vi.fn(async()=>{order.push('clear');})};
  const objects={artifactUrl:vi.fn().mockResolvedValue('https://r2.invalid/signed')};
  registerJobRoutes(app,config,()=>({jobs,assets,billing,bridge,signals,objects}));
  const post=(path='/v1/jobs',body:unknown=payload)=>app.request(path,{method:'POST',headers,body:JSON.stringify(body)});
  return {app,post,jobs,assets,billing,bridge,signals,objects,order,row};
}
it('authorizes before exact Python preflight and arms before committed admission and dispatch',async()=>{
  const s=setup();const res=await s.post();expect(res.status).toBe(202);
  expect(await res.json()).toEqual({sourceCover:true,sourceId:source,narratorVoiceId:voice,castingMode:'single',id,status:'queued',progress:{stage:'queued',completed:0,total:0}});
  expect(s.order).toEqual(['owned','preflight','account','arm','commit','dispatch']);
  expect(s.jobs.admitJob.mock.calls[0]![0]).toMatchObject({ownerId:owner,credits:2n,idempotencyKey:null});
  expect(s.bridge.preflight.mock.calls[0]![1]).toMatchObject(payload);
  expect(s.bridge.dispatch).toHaveBeenCalledWith(dispatchId);
});
it('preserves preflight numeric integer schema and exact large stored progress tokens',async()=>{
  const s=setup();const res=await s.post('/v1/jobs/preflight');
  expect(await res.text()).toBe('{"sourceId":"'+source+'","normalizedCharacters":3969,"valid":true,"estimatedCredits":2,"availableCredits":9007199254740993}');
  s.row.progressJson='{"stage":"running","completed":9007199254740993,"total":9007199254740995}';
  expect(await (await s.app.request('/v1/jobs/'+id,{headers})).text()).toContain('"completed":9007199254740993,"total":9007199254740995');
});
it('replays the original marker before clearing candidate and never dispatches replay',async()=>{
  const s=setup();s.row.replayed=true;
  expect((await s.post()).status).toBe(202);expect(s.order.slice(-4)).toEqual(['arm','commit','arm','clear']);
  expect(s.signals.arm.mock.calls[1]).toEqual([id]);expect(s.bridge.dispatch).not.toHaveBeenCalled();
});
it('does not retry failed admission or ambiguous dispatch',async()=>{
  const s=setup();s.jobs.admitJob.mockRejectedValueOnce(Error('lost commit'));
  expect((await s.post()).status).toBe(500);expect(s.jobs.admitJob).toHaveBeenCalledTimes(1);expect(s.bridge.dispatch).not.toHaveBeenCalled();
  s.bridge.dispatch.mockRejectedValueOnce(Error('lost delivery'));expect((await s.post()).status).toBe(500);
  expect(s.jobs.admitJob).toHaveBeenCalledTimes(2);expect(s.bridge.dispatch).toHaveBeenCalledTimes(1);expect(s.signals.clear).not.toHaveBeenCalled();
});
it('blocks foreign/expired/invalid sources before compute or credit operations',async()=>{
  for(const error of [new ApiError(403,'http_error','resource is not owned by this user'),new ApiError(404,'not_found','Not found')]){
    const s=setup();s.assets.requireOwned.mockRejectedValueOnce(error);expect((await s.post()).status).toBe(error.status);
    expect(s.bridge.preflight).not.toHaveBeenCalled();expect(s.billing.accountForOwner).not.toHaveBeenCalled();
  }
});
it('rejects invalid models, voices, chapter selections and strict speech options before compute',async()=>{
  for(const body of [{...payload,chapters:[]},{...payload,chapters:['one','one']},{...payload,casting:{voiceId:'unavailable'}},{...payload,casting:{voiceId:voice,modelId:'bad'}},{...payload,tts:{prepareNumbers:'true'}},{...payload,tts:{chapterPauseMs:1.5}},{...payload,tts:{scenePauseMs:60001}}]){
    const s=setup();expect((await s.post('/v1/jobs',body)).status).toBe(422);expect(s.bridge.preflight).not.toHaveBeenCalled();
  }
});
it('preserves public aliases, camel priority, ignored extras and bool coercion in strict bridge payload',async()=>{
  const s=setup();const body={sourceId:source,source_id:randomUUID(),chapters:['one'],casting:{voice_id:voice,ignored:true},ignored:'client-count',tts:{normalize_text:'false',prepare_numbers:true,ignored:99},output:{source_cover:'0',ignored:true}};
  expect((await s.post('/v1/jobs',body)).status).toBe(202);
  expect(s.bridge.preflight.mock.calls[0]![1]).toEqual({sourceId:source,chapters:['one'],casting:{voiceId:voice},tts:{normalizeText:false,prepareNumbers:true},output:{sourceCover:false}});
});
it('rejects stale inspection and speech bounds without admission',async()=>{
  const s=setup();s.bridge.preflight.mockRejectedValueOnce(new ApiError(409,'http_error','stale'));
  expect((await s.post()).status).toBe(409);expect(s.jobs.admitJob).not.toHaveBeenCalled();
  for(const count of [0,10000001]){
    s.bridge.preflight.mockResolvedValueOnce({sourceId:source,normalizedCharacters:count,valid:true,availableCredits:null,estimatedCredits:null});
    const res=await s.post();expect(res.status).toBe(422);expect((await res.json()).error.message).toBe(count?'job_size_limit':'empty_speech');
  }
});
it('keeps legacy redirect and additive artifact-url equally owned, completed, no-store and stored-path signed',async()=>{
  const s=setup();s.row.status='succeeded';
  const legacy=await s.app.request('/v1/jobs/'+id+'/artifact',{headers});expect(legacy.status).toBe(303);expect(legacy.headers.get('location')).toBe('https://r2.invalid/signed');expect(legacy.headers.get('cache-control')).toBe('no-store');
  const json=await s.app.request('/v1/jobs/'+id+'/artifact-url',{headers});expect(await json.json()).toEqual({url:'https://r2.invalid/signed'});expect(json.headers.get('cache-control')).toBe('no-store');
  expect(s.objects.artifactUrl).toHaveBeenCalledWith(id+'.'+dispatchId,id+'.m4b');
  s.row.status='queued';expect((await s.app.request('/v1/jobs/'+id+'/artifact-url',{headers})).status).toBe(409);
  s.jobs.getOwned.mockRejectedValueOnce(new ApiError(403,'http_error','resource is not owned by this user'));
  expect((await s.app.request('/v1/jobs/'+id+'/artifact',{headers})).status).toBe(403);expect(s.objects.artifactUrl).toHaveBeenCalledTimes(2);
});
it.each(['/v1/jobs','/v1/jobs/'+id,'/v1/jobs/'+id+'/artifact','/v1/jobs/'+id+'/artifact-url'])('rejects HEAD %s after auth and unsupported methods before services',async path=>{
  const s=setup();expect((await s.app.request(path,{method:'HEAD'})).status).toBe(401);
  expect((await s.app.request(path,{method:'HEAD',headers})).status).toBe(405);
  expect((await s.app.request(path,{method:'PUT',headers})).status).toBe(405);
  expect((await s.app.request('/v1/jobs/missing/deeper',{method:'PUT',headers})).status).toBe(404);
  expect(s.jobs.getOwned).not.toHaveBeenCalled();expect(s.jobs.listOwned).not.toHaveBeenCalled();expect(s.bridge.preflight).not.toHaveBeenCalled();
});

it('normalizes executable identifiers before the strict private bridge without accepting client counts',async()=>{
  const s=setup();const body={...payload,sourceId:' '+source+' ',chapters:[' one '],casting:{voiceId:' '+voice+' '},normalizedCharacters:1,estimatedCredits:0,accountId:'attacker'};
  expect((await s.post('/v1/jobs',body)).status).toBe(202);
  expect(s.bridge.preflight.mock.calls[0]![1]).toEqual(payload);
  expect(s.jobs.admitJob.mock.calls[0]![0]).toMatchObject({spec:jobSpec(payload),credits:2n});
});

it('bounds normalized private bridge options locally with codepoint lengths and explicit field paths',async()=>{
  const model=config.catalog.capabilities.casting.models[0]!;
  for(const [body,field,maximum] of [
    [{...payload,chapters:Array.from({length:10001},(_,i)=>String(i))},['chapters'],10000],
    [{...payload,chapters:['😀'.repeat(513)]},['chapters',0],512],
    [{...payload,casting:{voiceId:voice,modelId:model,method:'😀'.repeat(513)}},['casting','method'],512],
    [{...payload,casting:{voiceId:voice,modelId:model,cast:Object.fromEntries(Array.from({length:1001},(_,i)=>['c'+i,voice]))}},['casting','cast'],1000],
  ] as const){
    const s=setup();const res=await s.post('/v1/jobs',body);expect(res.status).toBe(422);
    expect((await res.json()).error).toMatchObject({code:'resource_limit',details:{field,maximum}});
    expect(s.bridge.preflight).not.toHaveBeenCalled();expect(s.jobs.admitJob).not.toHaveBeenCalled();
  }
  for(const chapters of [['😀'.repeat(511)],['😀'.repeat(512)],Array.from({length:10000},(_,i)=>String(i))]){
    const s=setup();expect((await s.post('/v1/jobs',{...payload,chapters})).status).toBe(202);expect(s.bridge.preflight).toHaveBeenCalledTimes(1);
  }
  for(const length of [999,1000]){
    const s=setup();expect((await s.post('/v1/jobs',{...payload,casting:{voiceId:voice,modelId:model,cast:Object.fromEntries(Array.from({length},(_,i)=>['c'+i,voice]))}})).status).toBe(202);
  }
});

it('returns method 405 for PATCH known jobs and GET cancel with auth preceding route dispatch and no SQL access',async()=>{
  const s=setup();
  for(const [path,method] of [['/v1/jobs/'+id,'PATCH'],['/v1/jobs/'+id+'/cancel','GET']]){
    expect((await s.app.request(path!,{method:method!})).status).toBe(401);
    const res=await s.app.request(path!,{method:method!,headers});
    expect(res.status).toBe(405);expect((await res.json()).error.code).toBe('method_not_allowed');
  }
  expect(s.jobs.getOwned).not.toHaveBeenCalled();expect(s.jobs.cancelOwned).not.toHaveBeenCalled();expect(s.jobs.admitJob).not.toHaveBeenCalled();
});

it('rejects authenticated GET static preflight before dynamic job lookup or any dependency',async()=>{
  const s=setup();
  expect((await s.app.request('/v1/jobs/preflight')).status).toBe(401);
  const res=await s.app.request('/v1/jobs/preflight',{headers});
  expect(res.status).toBe(405);expect((await res.json()).error.code).toBe('method_not_allowed');
  expect(s.jobs.getOwned).not.toHaveBeenCalled();expect(s.assets.requireOwned).not.toHaveBeenCalled();expect(s.billing.accountForOwner).not.toHaveBeenCalled();expect(s.bridge.preflight).not.toHaveBeenCalled();
});
it('counts all normalized selected cast pairs before collapsing whitespace variants into the private object',async()=>{
  const s=setup(),model=config.catalog.capabilities.casting.models[0]!;
  const cast={...Object.fromEntries(Array.from({length:1000},(_,i)=>['character'+i,voice])),' character0 ':voice};
  const res=await s.post('/v1/jobs',{...payload,casting:{voiceId:voice,modelId:model,cast}});
  expect(res.status).toBe(422);expect((await res.json()).error).toMatchObject({code:'resource_limit',details:{field:['casting','cast'],maximum:1000}});
  expect(s.bridge.preflight).not.toHaveBeenCalled();expect(s.jobs.admitJob).not.toHaveBeenCalled();
});
it('reports overlong selected model before allowlist checking and retains ordinary model errors',async()=>{
  for(const modelId of ['x'.repeat(513),'😀'.repeat(513),'  '+'😀'.repeat(513)+'  ']){
    const s=setup(),res=await s.post('/v1/jobs',{...payload,casting:{voiceId:voice,modelId}});
    expect(res.status).toBe(422);expect((await res.json()).error).toMatchObject({code:'resource_limit',details:{field:['casting','modelId'],maximum:512}});
    expect(s.bridge.preflight).not.toHaveBeenCalled();expect(s.jobs.admitJob).not.toHaveBeenCalled();
  }
  for(const modelId of ['unavailable','x'.repeat(512),'😀'.repeat(512),'  '+'😀'.repeat(512)+'  ']){
    const s=setup(),res=await s.post('/v1/jobs',{...payload,casting:{voiceId:voice,modelId}});
    expect(res.status).toBe(422);expect((await res.json()).error.message).toBe('model_not_allowed');expect(s.bridge.preflight).not.toHaveBeenCalled();
  }
});
