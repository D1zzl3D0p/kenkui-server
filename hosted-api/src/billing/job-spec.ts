import { integerText } from './integers.js';
import { createHash } from 'node:crypto';
import type { StrictJob } from '../compute/modal.js';
import { ApiError } from '../types.js';

export function creditsForCharacters(count:number|bigint, multivoice:boolean, cost=process.env.KENKUI_ESTIMATED_COST_CENTS_PER_MILLION??'126'):bigint {
  if ((typeof count==='number' && !Number.isSafeInteger(count)) || count<0) throw Error('invalid_character_count');
  const cents=integerText(cost);
  if(cents===null||cents<1n)throw Error('invalid_estimated_processing_cost');
  return (BigInt(count)*cents*(multivoice?3n:2n)+999999n)/1000000n;
}
export function scopedKey(owner:string,key:string):string {
  return createHash('sha256').update(owner+':'+key).digest('hex');
}
const invalid=(message:string)=>new ApiError(422,'http_error',message);
const strip=(s:string)=>s.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu,'');
function required(s:string,code:string):string {
  const value=strip(s);
  if(!value)throw invalid(code);
  return value;
}
// UTF-8 binary order preserves Unicode scalar order, unlike UTF-16 sort.
const compare=(a:string,b:string)=>Buffer.compare(Buffer.from(a),Buffer.from(b));
type Casting={kind:'single';voice_id:string}|{kind:'characters';narrator_voice_id:string;unknown_voice_id:string;
  model_id:string;method:string;cast:string[][]};
const defaultTts={normalize_text:true,chapter_pauses:false,prepare_numbers:false,pronunciation_corrections:false,stutter_handling:false,
  chapter_pause_ms:null as number|null,heading_before_pause_ms:0,heading_after_pause_ms:0,paragraph_pause_ms:0,line_pause_ms:0,scene_pause_ms:0};
export interface JobSpec {source_id:string;chapters:string[];casting:Casting;tts:typeof defaultTts;
  output:{path:string;title:string|null;author:string|null;source_cover:boolean}}
function normalize(value:JobSpec):JobSpec {
  const source_id=required(value.source_id,'invalid_source_id');
  const chapters=value.chapters.map(c=>required(c,'invalid_chapter_id'));
  if(!chapters.length)throw invalid('empty_chapter_selection');
  if(new Set(chapters).size!==chapters.length)throw invalid('duplicate_chapter_id');
  const c=value.casting;
  let casting:Casting;
  if(c.kind==='characters'){
    const narrator=required(c.narrator_voice_id,'invalid_voice_id');
    const cast=c.cast.map(([a,b])=>[required(a!,'invalid_character_id'),required(b!,'invalid_voice_id')]);
    cast.sort(([a,b],[x,y])=>compare(a!,x!)||compare(b!,y!));
    casting={kind:'characters',narrator_voice_id:narrator,unknown_voice_id:strip(c.unknown_voice_id)||narrator,
      model_id:required(c.model_id,'invalid_model_id'),method:required(c.method,'invalid_method'),cast};
  }else casting={kind:'single',voice_id:required(c.voice_id,'invalid_voice_id')};
  return {source_id,chapters,casting,tts:{...defaultTts,...value.tts},
    output:{...{title:null,author:null,source_cover:true},...value.output,path:required(value.output.path,'invalid_output_path')}};
}
/** Call after the route/bridge has validated the request. Preserve Python dataclass defaults on replay. */
export function decodeSpec(raw:string):JobSpec { return normalize(JSON.parse(raw)); }
export function jobSpec(job:StrictJob,allowedModels:readonly string[]=[]):JobSpec {
  const c=job.casting,t=job.tts??{},o=job.output??{};
  if((o.format??'m4b')!=='m4b')throw invalid('only m4b output is supported');
  let casting:Casting;
  if(c.modelId==null){
    if(c.voiceId==null)throw invalid('a voice is required');
    casting={kind:'single',voice_id:c.voiceId};
  }else {
    if(!allowedModels.includes(c.modelId))throw invalid('model_not_allowed');
    const narrator=c.narratorVoiceId||c.voiceId;
    if(narrator==null)throw invalid('a narrator voice is required');
    casting={kind:'characters',narrator_voice_id:narrator,unknown_voice_id:c.unknownVoiceId??'',model_id:c.modelId,
      method:c.method??'gendered',cast:Object.entries(c.cast??{})};
  }
  return normalize({source_id:job.sourceId,chapters:job.chapters,casting,tts:{normalize_text:true,chapter_pauses:t.chapterPauses??false,
    prepare_numbers:t.prepareNumbers??false,pronunciation_corrections:t.pronunciationCorrections??false,stutter_handling:t.stutterHandling??false,
    chapter_pause_ms:t.chapterPauseMs??null,heading_before_pause_ms:t.headingBeforePauseMs??0,heading_after_pause_ms:t.headingAfterPauseMs??0,
    paragraph_pause_ms:t.paragraphPauseMs??0,line_pause_ms:t.linePauseMs??0,scene_pause_ms:t.scenePauseMs??0},
    output:{path:'artifact.m4b',title:o.title??null,author:o.author??null,source_cover:o.sourceCover??true}});
}
export function encodeSpec(spec:JobSpec):string {
  // All object keys are fixed ASCII field names. Cast names are array elements.
  return JSON.stringify(spec,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)
    ?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v)
    .replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
