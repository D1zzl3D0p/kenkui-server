import { JsonIntegerTooLong,parseExactJson } from '../billing/integers.js';
import type { StrictJob } from '../compute/modal.js';
import { ApiError } from '../types.js';

type Issue={type:string;loc:(string|number)[];msg:string;input:unknown;ctx?:Record<string,unknown>};
export const rawInteger=(value:string|bigint)=>(JSON as JSON & {rawJSON(s:string):unknown}).rawJSON(String(value));
export function exactValue(value:unknown):unknown {
  if(typeof value==='bigint')return rawInteger(value);
  if(Array.isArray(value))return value.map(exactValue);
  if(value!==null&&typeof value==='object')return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,exactValue(v)]));
  return value;
}
export const validation=(issues:Issue[])=>new ApiError(422,'validation_error','Request validation failed',{errors:issues.map(i=>({...i,input:exactValue(i.input)}))});
export const record=(x:unknown):x is Record<string,unknown>=>typeof x==='object'&&x!==null&&!Array.isArray(x);
export function publicBody(raw:string,contentType:string|undefined):unknown {
  if(!raw)throw validation([{type:'missing',loc:['body'],msg:'Field required',input:null}]);
  const mime=contentType?.split(';')[0]?.trim().toLowerCase();
  if(mime&&!/^application\/(?:json|[\w.+-]+\+json)$/.test(mime))return raw;
  try {
    const value=parseExactJson(raw);
    if(value===null)throw validation([{type:'missing',loc:['body'],msg:'Field required',input:null}]);
    return value;
  }catch(error){
    if(error instanceof ApiError)throw error;
    if(error instanceof JsonIntegerTooLong)throw new ApiError(400,'http_error','There was an error parsing the body');
    const match=error instanceof SyntaxError?/^(.+) at position (\d+) \(line \d+ column \d+\)$/.exec(error.message):null;
    const reasons:Record<string,string>={"Expected property name or '}' in JSON":'Expecting property name enclosed in double quotes','Expected double-quoted property name in JSON':'Expecting property name enclosed in double quotes',"Expected ':' after property name in JSON":"Expecting ':' delimiter",'Unexpected non-whitespace character after JSON':'Extra data',"Expected ',' or ']' after array element in JSON":"Expecting ',' delimiter"};
    const reason=match&&reasons[match[1]!];
    throw validation([{type:'json_invalid',loc:['body',reason?Array.from(raw.slice(0,Number(match![2]))).length:0],msg:'JSON decode error',input:{},ctx:{error:reason||'Expecting value'}}]);
  }
}
type Field={alias?:string;type:'string'|'strings'|'cast'|'boolean'|'strictBoolean'|'integer';required?:boolean;nullable?:boolean;maxLength?:number;minLength?:number};
function parseModel(value:unknown,fields:Record<string,Field>,loc:(string|number)[],issues:Issue[]):Record<string,unknown> {
  if(!record(value)){issues.push({type:'model_attributes_type',loc,msg:'Input should be a valid dictionary or object to extract fields from',input:value});return {};}
  const output:Record<string,unknown>={};
  for(const [key,field] of Object.entries(fields)){
    const supplied=Object.hasOwn(value,key)?key:field.alias&&Object.hasOwn(value,field.alias)?field.alias:undefined;
    const here=[...loc,supplied??key];
    if(supplied===undefined){if(field.required)issues.push({type:'missing',loc:here,msg:'Field required',input:value});continue;}
    let selected=value[supplied];
    if(selected===null&&field.nullable){output[key]=null;continue;}
    const fail=(type:string,msg:string,ctx?:Record<string,unknown>)=>issues.push({type,loc:here,msg,input:selected,...(ctx?{ctx}:{})});
    if(field.type==='string'){
      if(typeof selected!=='string'){fail('string_type','Input should be a valid string');continue;}
      const length=Array.from(selected).length;
      if(field.minLength&&length<field.minLength){fail('string_too_short',`String should have at least ${field.minLength} character`,{min_length:field.minLength});continue;}
      if(field.maxLength&&length>field.maxLength){fail('string_too_long',`String should have at most ${field.maxLength} characters`,{max_length:field.maxLength});continue;}
    }else if(field.type==='strictBoolean'){
      if(typeof selected!=='boolean'){fail('bool_type','Input should be a valid boolean');continue;}
    }else if(field.type==='boolean'){
      if(typeof selected!=='boolean'){
        const text=typeof selected==='string'?selected.toLowerCase():selected===0n||selected===1n||selected===0||selected===1?String(selected):'';
        if(['1','true','t','yes','y','on'].includes(text))selected=true;
        else if(['0','false','f','no','n','off'].includes(text))selected=false;
        else {
          const parsing=typeof selected==='string'||typeof selected==='bigint'||(typeof selected==='number'&&Number.isInteger(selected));
          fail(parsing?'bool_parsing':'bool_type',parsing?'Input should be a valid boolean, unable to interpret input':'Input should be a valid boolean');continue;
        }
      }
    }else if(field.type==='integer'){
      if(typeof selected!=='bigint'){fail('int_type','Input should be a valid integer');continue;}
      if(selected<0n){fail('greater_than_equal','Input should be greater than or equal to 0',{ge:0});continue;}
      if(selected>60000n){fail('less_than_equal','Input should be less than or equal to 60000',{le:60000});continue;}
      selected=Number(selected);
    }else if(field.type==='strings'){
      if(!Array.isArray(selected)){fail('list_type','Input should be a valid list');continue;}
      selected.forEach((item,index)=>{if(typeof item!=='string')issues.push({type:'string_type',loc:[...here,index],msg:'Input should be a valid string',input:item});});
    }else if(field.type==='cast'){
      if(!record(selected)){fail('dict_type','Input should be a valid dictionary');continue;}
      for(const [name,voice] of Object.entries(selected))if(typeof voice!=='string')issues.push({type:'string_type',loc:[...here,name],msg:'Input should be a valid string',input:voice});
    }
    output[key]=selected;
  }
  return output;
}
const text=(alias?:string,nullable=false):Field=>({type:'string',nullable,...(alias?{alias}:{})});
/** Public Pydantic compatibility; emit only canonical camel fields for the strict private bridge. */
export function jobRequest(raw:string,contentType:string|undefined):StrictJob {
  const value=publicBody(raw,contentType),issues:Issue[]=[];
  if(!record(value))throw validation([{type:'model_attributes_type',loc:['body'],msg:'Input should be a valid dictionary or object to extract fields from',input:value}]);
  const job=parseModel(value,{sourceId:{...text('source_id'),required:true},chapters:{type:'strings',required:true}},['body'],issues);
  const nested=(name:string,fields:Record<string,Field>,required=false)=>{
    if(!Object.hasOwn(value,name)){if(required)issues.push({type:'missing',loc:['body',name],msg:'Field required',input:value});return;}
    job[name]=parseModel(value[name],fields,['body',name],issues);
  };
  nested('casting',{voiceId:text('voice_id',true),narratorVoiceId:text('narrator_voice_id',true),unknownVoiceId:text('unknown_voice_id',true),cast:{type:'cast'},method:text(),modelId:text('model_id',true)},true);
  nested('tts',Object.fromEntries([
    ['normalizeText',{type:'boolean',alias:'normalize_text'}],
    ...['chapterPauses','prepareNumbers','pronunciationCorrections','stutterHandling'].map(key=>[key,{type:'strictBoolean',alias:key.replace(/[A-Z]/g,c=>'_'+c.toLowerCase())}]),
    ...['chapterPauseMs','headingBeforePauseMs','headingAfterPauseMs','paragraphPauseMs','linePauseMs','scenePauseMs'].map(key=>[key,{type:'integer',alias:key.replace(/[A-Z]/g,c=>'_'+c.toLowerCase()),nullable:key==='chapterPauseMs'}]),
  ]) as Record<string,Field>);
  nested('output',{format:text(),title:{...text(undefined,true),minLength:1,maxLength:500},author:{...text(undefined,true),minLength:1,maxLength:500},sourceCover:{type:'boolean',alias:'source_cover'}});
  if(issues.length)throw validation(issues);
  return job as unknown as StrictJob;
}

const resourceLimit=(field:(string|number)[],maximum:number)=>{throw new ApiError(422,'resource_limit','Job options exceed document processing limits.',{field,maximum});};
/** Count selected pairs before the bridge object can collapse normalized duplicate names. */
export function selectedCastingLimits(job:StrictJob):void {
  if(job.casting.modelId===null||job.casting.modelId===undefined)return;
  const model=job.casting.modelId.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu,'');
  if(Array.from(model).length>512)resourceLimit(['casting','modelId'],512);
  if(Object.keys(job.casting.cast??{}).length>1000)resourceLimit(['casting','cast'],1000);
}
/** Public migration bounds intentionally match the existing private Python bridge. */
export function bridgeLimits(job:StrictJob,owner:string,environment:string):void {
  const limit=resourceLimit;
  const short=(value:string|undefined|null,field:(string|number)[])=>{if(value!==undefined&&value!==null&&Array.from(value).length>512)limit(field,512);};
  if(job.chapters.length>10000)limit(['chapters'],10000);
  job.chapters.forEach((chapter,index)=>short(chapter,['chapters',index]));
  for(const key of ['voiceId','narratorVoiceId','unknownVoiceId','modelId','method'] as const)short(job.casting[key],['casting',key]);
  const cast=Object.entries(job.casting.cast??{});
  if(cast.length>1000)limit(['casting','cast'],1000);
  for(const [character,voice] of cast){short(character,['casting','cast',character,'key']);short(voice,['casting','cast',character]);}
  // Bridge MAX_REQUEST_BYTES: base64(8 MiB) plus 64 KiB envelope allowance.
  if(Buffer.byteLength(JSON.stringify({environment,ownerId:owner,job}))>11250348)limit(['body'],11250348);
}
