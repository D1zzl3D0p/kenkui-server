import { getRequestListener } from '@hono/node-server';
import { createServer } from 'node:http';
import { pathToFileURL } from 'node:url';
import handler from './index.js';
import { closePool } from './db/pool.js';

/** Adapter only: the Function handler owns identical Neon auth, identity and DB wiring. */
export function serveApi(api:{fetch(request:Request):Response|Promise<Response>}=handler,options:{port?:number;hostname?:string}={}) {
  return createServer(getRequestListener(request=>api.fetch(request))).listen(options.port??8080,options.hostname??'0.0.0.0');
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href) {
  const port=Number(process.env.PORT??'8080');
  if(!Number.isInteger(port)||port<1||port>65535)throw Error('Invalid port');
  const server=serveApi(handler,{port,hostname:'0.0.0.0'});
  let closing=false;
  const shutdown=()=>{
    if(closing)return;closing=true;
    // Stop admission, allow bounded draining, then close active SSE/download sockets.
    const deadline=setTimeout(()=>server.closeAllConnections(),10_000);deadline.unref();
    server.close(()=>{clearTimeout(deadline);void closePool().catch(()=>{process.exitCode=1;});});
  };
  process.on('SIGTERM',shutdown);process.on('SIGINT',shutdown);
}
