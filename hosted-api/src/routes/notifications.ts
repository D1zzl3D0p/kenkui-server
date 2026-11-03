import { createHmac,timingSafeEqual } from 'node:crypto';
import type { Hono } from 'hono';
import type { NotificationStore } from '../db/notifications.js';
import { ApiError,type ApiEnv } from '../types.js';
import { getOnly } from './get-only.js';
import { publicBody,record,validation } from './job-request.js';

function identityUuid(value:string):string|null {
  // UUID(string) accepts canonical, compact, urn and brace forms and normalizes case.
  const compact=value.replace(/^urn:uuid:/,'').replace(/^\{(.*)\}$/,'$1').replaceAll('-','');
  if(!/^[0-9a-fA-F]{32}$/.test(compact))return null;
  return compact.toLowerCase().replace(/^(.{8})(.{4})(.{4})(.{4})(.{12})$/,'$1-$2-$3-$4-$5');
}
export function registerNotificationRoutes(app:Hono<ApiEnv>,store:Pick<NotificationStore,'settings'|'setEnabled'>,secret:string):void {
  app.get('/v1/notifications',getOnly,async c=>c.json(await store.settings(c.get('principal').userId)));
  app.patch('/v1/notifications',async c=>{
    const value=publicBody(await c.req.text(),c.req.header('Content-Type'));
    if(!record(value))throw validation([{type:'model_attributes_type',loc:['body'],msg:'Input should be a valid dictionary or object to extract fields from',input:value}]);
    if(!Object.hasOwn(value,'emailOnCompletion'))throw validation([{type:'missing',loc:['body','emailOnCompletion'],msg:'Field required',input:value}]);
    if(typeof value.emailOnCompletion!=='boolean')throw validation([{type:'bool_type',loc:['body','emailOnCompletion'],msg:'Input should be a valid boolean',input:value.emailOnCompletion}]);
    const owner=c.get('principal').userId;
    await store.setEnabled(owner,value.emailOnCompletion);return c.json(await store.settings(owner));
  });
  app.get('/v1/notifications/unsubscribe',getOnly,async c=>{
    const issues=['identity','token'].filter(key=>c.req.query(key)===undefined).map(key=>({type:'missing',loc:['query',key],msg:'Field required',input:null}));
    if(issues.length)throw validation(issues);
    const owner=identityUuid(c.req.query('identity')!),token=c.req.query('token')!;
    const digest=owner&&secret?createHmac('sha256',secret).update('kenkui-notification-unsubscribe-v1:'+owner).digest('hex'):'';
    const supplied=Buffer.from(token),expected=Buffer.from(digest);
    if(!digest||supplied.length!==expected.length||!timingSafeEqual(supplied,expected))throw new ApiError(400,'http_error','invalid_unsubscribe_link');
    await store.setEnabled(owner!,false);
    return c.html('<html><body><p>You will no longer receive email when a book finishes. You can turn this back on in Kenkui Studio.</p></body></html>');
  });
  for(const path of ['/v1/notifications','/v1/notifications/unsubscribe'])app.all(path,()=>{throw new ApiError(405,'method_not_allowed','Method not allowed');});
}
