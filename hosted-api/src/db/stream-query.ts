import type { Pool,PoolClient,QueryResultRow } from 'pg';

/** One bounded stream read. No client remains checked out after resolution or abort. */
export async function streamQuery<T extends QueryResultRow>(pool:()=>Pool,text:string,values:unknown[],signal?:AbortSignal):Promise<T[]> {
  signal?.throwIfAborted();
  return new Promise<T[]>((resolve,reject)=>{
    let client:PoolClient|undefined,settled=false,released=false;
    const release=(destroy=false)=>{if(client&&!released){released=true;client.release(destroy);}};
    const finish=(error:unknown,rows?:T[])=>{
      if(settled)return;
      settled=true;clearTimeout(deadline);signal?.removeEventListener('abort',abort);
      release(Boolean(error));
      if(error)reject(error);else resolve(rows!);
    };
    const abort=()=>finish(signal!.reason??new Error('Stream aborted'));
    // Includes time waiting for checkout; never change global/financial query timeouts.
    const deadline=setTimeout(()=>finish(new Error('Stream query deadline exceeded')),5000);
    signal?.addEventListener('abort',abort,{once:true});
    try {
      void pool().connect().then(async checkedOut=>{
        client=checkedOut;
        if(settled){release(true);return;}
        try {finish(undefined,(await client.query<T>(text,values)).rows);}
        catch(error){finish(error);}
      },error=>finish(error));
    }catch(error){finish(error);}
  });
}
