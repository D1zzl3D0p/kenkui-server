import { once } from 'node:events';
import { expect,it } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { serveApi } from '../src/standalone.js';

it('runs identical injected Function contracts through the real Node adapter, including cancellation',async()=>{
  const config=loadConfig({KENKUI_ENV:'staging',KENKUI_ALLOWED_ORIGINS:'https://studio.invalid',KENKUI_ORIGIN_SECRET:'synthetic'});
  const principal={userId:'owner',issuer:'https://auth.invalid/db/auth',subject:'verified'};
  const app=createApi({config,getPool:()=>{throw Error('unexpected DB');},auth:{verify:async()=>principal}});
  let cancelled=0;
  app.get('/v1/jobs/contract',c=>c.json(c.get('principal')));
  app.get('/stream',()=>new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('first\n'));},cancel(){cancelled++;}},{highWaterMark:0})));
  const server=serveApi(app,{hostname:'127.0.0.1',port:0});await once(server,'listening');const address=server.address();if(!address||typeof address==='string')throw Error('No listener');
  const headers={'X-Kenkui-Origin-Secret':'synthetic',Origin:'https://studio.invalid'};
  try {
    for(const [path,extra] of [['/v1/health',{}],['/v1/jobs/contract',{}],['/v1/jobs/contract',{Authorization:'Bearer synthetic'}],['/missing',{}]] as const){
      const direct=await app.fetch(new Request('http://synthetic'+path,{headers:{...headers,...extra}}));
      const node=await fetch(`http://127.0.0.1:${address.port}`+path,{headers:{...headers,...extra}});
      expect(node.status).toBe(direct.status);expect(node.headers.get('access-control-allow-origin')).toBe(direct.headers.get('access-control-allow-origin'));
      const normalize=(value:string)=>value.replace(/"requestId":"[^"]+"/g,'"requestId":"id"');expect(normalize(await node.text())).toBe(normalize(await direct.text()));
    }
    const abort=new AbortController(),stream=await fetch(`http://127.0.0.1:${address.port}/stream`,{headers,signal:abort.signal}),reader=stream.body!.getReader();
    expect(new TextDecoder().decode((await reader.read()).value)).toBe('first\n');abort.abort();await reader.cancel().catch(()=>{});
    await expect.poll(()=>cancelled).toBe(1);
  } finally {server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
});
