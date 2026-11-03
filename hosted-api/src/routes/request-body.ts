import { ApiError } from '../types.js';

const fail=(status:413|422,message:string)=>new ApiError(status,'http_error',message);
export async function readBoundedBody(request:Request,max:number,limitMessage:string):Promise<Buffer> {
  const declared=request.headers.get('content-length');
  if(declared&&(!/^\d+$/.test(declared)||!Number.isSafeInteger(Number(declared))))throw fail(422,'Invalid content length.');
  if(declared&&Number(declared)>max)throw fail(413,limitMessage);
  const reader=request.body?.getReader();
  let bytes=Buffer.alloc(0),size=0;
  try {
    if(reader)for(;;){
      const {done,value}=await reader.read();
      if(done)break;
      const next=size+value.byteLength;
      if(next>max){
        void reader.cancel().catch(()=>{});
        throw fail(413,limitMessage);
      }
      // Grow only as bytes arrive and bound per-chunk overhead on highly fragmented input.
      if(next>bytes.length){
        const grown=Buffer.allocUnsafe(Math.min(max,Math.max(next,4096,2*bytes.length)));
        bytes.copy(grown,0,0,size);
        bytes=grown;
      }
      bytes.set(value,size);
      size=next;
    }
  }catch(error){if(error instanceof ApiError)throw error;throw fail(422,'Incomplete upload.');}
  finally{reader?.releaseLock();}
  if(declared&&size!==Number(declared))throw fail(422,'Incomplete upload.');
  return bytes.subarray(0,size);
}

export async function boundedText(request:Request,limit:number):Promise<string> {
  return new TextDecoder().decode(await readBoundedBody(request,limit,'Request is too large.'));
}
