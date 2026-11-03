import { createHmac,randomUUID } from 'node:crypto';
import { expect,it,vi } from 'vitest';
import { createApi } from '../src/app.js';
import { loadConfig } from '../src/config.js';
import { registerNotificationRoutes } from '../src/routes/notifications.js';
const owner=randomUUID(),secret='synthetic secret',headers={Authorization:'Bearer fixture','Content-Type':'application/json'};
function setup(){
  const app=createApi({config:loadConfig({KENKUI_ENV:'staging'}),getPool(){throw Error('unexpected');},auth:{async verify(){return {userId:owner,issuer:'fixture',subject:'fixture'};}}});
  const store={settings:vi.fn().mockResolvedValue({email:'old@example.invalid',emailOnCompletion:true}),setEnabled:vi.fn().mockResolvedValue(undefined)};
  registerNotificationRoutes(app,store,secret);return {app,store};
}
it('rejects oversized preference changes before touching a store',async()=>{
  const {app,store}=setup();
  const res=await app.request('/v1/notifications',{method:'PATCH',headers,body:' '.repeat(8193)});
  expect(res.status).toBe(413);expect(store.setEnabled).not.toHaveBeenCalled();expect(store.settings).not.toHaveBeenCalled();
});
it('reads and updates only the mapped internal identity, retaining its existing email',async()=>{
  const {app,store}=setup();expect(await (await app.request('/v1/notifications',{headers})).json()).toEqual({email:'old@example.invalid',emailOnCompletion:true});
  expect(store.settings).toHaveBeenCalledWith(owner);
  expect((await app.request('/v1/notifications',{method:'PATCH',headers,body:JSON.stringify({emailOnCompletion:false,email:'attacker@example.invalid'})})).status).toBe(200);
  expect(store.setEnabled).toHaveBeenCalledWith(owner,false);
  for(const value of ['false',0,null])expect((await app.request('/v1/notifications',{method:'PATCH',headers,body:JSON.stringify({emailOnCompletion:value})})).status).toBe(422);
  expect(store.setEnabled).toHaveBeenCalledTimes(1);
});
it('honors the exact existing signed unsubscribe algorithm without a session or mail',async()=>{
  const {app,store}=setup();const token=createHmac('sha256',secret).update('kenkui-notification-unsubscribe-v1:'+owner).digest('hex');
  const res=await app.request('/v1/notifications/unsubscribe?identity='+owner.toUpperCase()+'&token='+token);
  expect(res.status).toBe(200);expect(await res.text()).toBe('<html><body><p>You will no longer receive email when a book finishes. You can turn this back on in Kenkui Studio.</p></body></html>');
  expect(store.setEnabled).toHaveBeenCalledWith(owner,false);
  const invalid=await app.request('/v1/notifications/unsubscribe?identity='+owner+'&token=invalid');expect(invalid.status).toBe(400);expect((await invalid.json()).error.message).toBe('invalid_unsubscribe_link');
  expect(store.setEnabled).toHaveBeenCalledTimes(1);
});
it('enforces auth/GET-only and known method parity without touching preferences',async()=>{
  const {app,store}=setup();expect((await app.request('/v1/notifications',{method:'HEAD'})).status).toBe(401);
  for(const [path,method] of [['/v1/notifications','HEAD'],['/v1/notifications','POST'],['/v1/notifications/unsubscribe','HEAD']])expect((await app.request(path!,{method:method!,headers})).status).toBe(405);
  expect((await app.request('/v1/notifications/unknown',{headers})).status).toBe(404);expect(store.settings).not.toHaveBeenCalled();expect(store.setEnabled).not.toHaveBeenCalled();
});
