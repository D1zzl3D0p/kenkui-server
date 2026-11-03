import { execFileSync } from 'node:child_process';
import { expect,it } from 'vitest';
import { jobRequest } from '../src/routes/job-request.js';
const payload={sourceId:'00000000-0000-0000-0000-000000000001',chapters:['one'],casting:{voiceId:'narrator'}};
it('matches actual Python public schema aliases, ignored extras, coercions, strict types and validation errors',()=>{
  for(const value of [payload,{}, {...payload,source_id:'ignored',unknown:99,casting:{voice_id:'narrator',unused:'ignored'},tts:{normalize_text:'off',prepare_numbers:true},output:{source_cover:'yes'}},
    {...payload,tts:{chapterPauseMs:0}}, {...payload,tts:{prepareNumbers:'false'}},{...payload,tts:{scenePauseMs:60001}}, {...payload,chapters:[true]},
    {...payload,casting:null}, {...payload,output:{title:''}}, {...payload,tts:null}, {...payload,sourceId:99}]){
    const raw=JSON.stringify(value),python=JSON.parse(execFileSync('../.venv/bin/python',['test/helpers/job_request.py'],{input:raw,encoding:'utf8'}));
    if(python.ok)expect(jobRequest(raw,'application/json')).toEqual(python.ok);
    else {
      let actual:unknown;try{jobRequest(raw,'application/json');}catch(e){actual=(e as {details:{errors:unknown}}).details.errors;}
      expect(JSON.parse(JSON.stringify(actual))).toEqual(python.errors.map((x:{loc:unknown[]})=>({...x,loc:['body',...x.loc]})));
    }
  }
});
