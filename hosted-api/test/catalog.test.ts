import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '../src/app.js';
import { ApiError } from '../src/types.js';
import { loadConfig } from '../src/config.js';
import type { Capabilities, Voice } from '../src/types.js';

const baseline = JSON.parse(readFileSync(new URL('../../contracts/neon/baseline.json', import.meta.url), 'utf8'));
const cases = baseline.contract.http as Array<{name: string; method: string; status: number; body: unknown; headers: Record<string,string>; request: {path:string; headers:Record<string,string>}}>;
const fixture = (name: string) => cases.find(x => x.name === name)!;
const config = () => ({
  environment: 'staging' as const,
  allowedOrigins: ['https://studio.example.invalid'],
  catalog: {
    capabilities: fixture('capabilities').body as Capabilities,
    voices: (fixture('voices').body as {items: Voice[]}).items,
  },
});
const requestId = () => '00000000-0000-0000-0000-000000000005';

describe('frozen public contract', () => {
  it('health_and_catalog_do_not_connect', async () => {
    const getPool = () => { throw new Error('catalog attempted database construction'); };
    const auth = { verify: async () => { throw new Error('catalog attempted authentication'); } };
    const app = createApi({ config: config(), getPool, auth });
    for (const name of ['health', 'capabilities', 'voices']) {
      const f = fixture(name);
      const res = await app.request(f.request.path, {headers:f.request.headers});
      expect(res.status).toBe(f.status);
      expect(await res.json()).toEqual(f.body);
      for (const [k,v] of Object.entries(f.headers)) expect(res.headers.get(k)).toBe(v);
    }
  });

  it.each(['missing-auth', 'invalid-auth', 'cors-missing-auth', 'cors-options'])('%s matches frozen request/status/body/headers', async name => {
    const f = fixture(name);
    const app = createApi({ config: config(), getPool: () => { throw new Error('unauthorized DB work'); }, auth: {verify: async () => null}, requestId });
    const res = await app.request(f.request.path, {method:f.method, headers:f.request.headers});
    expect(res.status).toBe(f.status);
    expect(f.method === 'OPTIONS' ? await res.text() : await res.json()).toEqual(f.body);
    for (const [k,v] of Object.entries(f.headers)) expect(res.headers.get(k)).toBe(v);
  });

  it('keeps canonical 429 errors, Retry-After and CORS without leaking exception messages', async () => {
    const logs: unknown[] = [];
    const app = createApi({config:config(), getPool:() => {throw new Error('unused');}, log: e => logs.push(e), requestId});
    app.get('/limited', () => {throw new ApiError(429, 'http_error', 'Too many requests', {}, {'Retry-After':'7'});});
    app.get('/broken', () => {throw new Error('postgresql://secret@private/database');});
    const res = await app.request('/limited', {headers:{origin:'https://studio.example.invalid','x-request-id':'client-id'}});
    expect(res.status).toBe(429);
    expect(res.headers.get('retry-after')).toBe('7');
    expect(res.headers.get('access-control-allow-origin')).toBe('https://studio.example.invalid');
    expect(await res.json()).toEqual({error:{code:'http_error', message:'Too many requests', details:{}, requestId:'client-id'}});
    const broken = await app.request('/broken');
    expect(broken.status).toBe(500);
    expect(await broken.json()).toEqual({error:{code:'internal_server_error',message:'Internal server error',details:{},requestId:requestId()}});
    expect(JSON.stringify(logs)).not.toContain('secret');
  });

  it('origin ingress credential is checked before body parsing, user auth or database work', async () => {
    const verify = vi.fn(async () => null);
    const app = createApi({config:{...config(), ingressSecret:'synthetic-origin-secret'}, getPool:() => {throw new Error('DB');}, auth:{verify}, requestId});
    app.post('/parse', async c => c.json(await c.req.json()));
    const res = await app.request('/parse', {method:'POST',body:'invalid JSON','headers':{'x-kenkui-origin-secret':'wrong'}});
    expect(res.status).toBe(403);
    expect((await res.json()).error.code).toBe('invalid_origin');
    expect(verify).not.toHaveBeenCalled();
    const valid = await app.request('/v1/health', {headers:{'x-kenkui-origin-secret':'synthetic-origin-secret'}});
    expect(valid.status).toBe(200);
    const options = fixture('cors-options');
    expect((await app.request('/v1/jobs',{method:'OPTIONS',headers:options.request.headers})).status).toBe(403);
    expect((await app.request('/v1/jobs',{method:'OPTIONS',headers:{...options.request.headers,'x-kenkui-origin-secret':'synthetic-origin-secret'}})).status).toBe(200);
  });

  it('protects known resource and session routes and passes only a verified principal', async () => {
    const principal = {userId:'00000000-0000-0000-0000-000000000001', issuer:'https://auth.example.invalid',subject:'verified-subject'};
    const app = createApi({config:config(),getPool:() => {throw new Error('unused');},auth:{verify:async () => principal}});
    app.get('/v1/jobs', c => c.json(c.get('principal')));
    expect(await (await app.request('/v1/jobs',{headers:{authorization:'Bearer synthetic'}})).json()).toEqual(principal);
    expect(await (await app.request('/v1/jobs',{headers:{cookie:'__Host-kenkui-session=synthetic'}})).json()).toEqual(principal);
    const unconfigured = createApi({config:config(),getPool:() => {throw new Error('unused');}});
    expect((await unconfigured.request('/v1/jobs',{headers:{authorization:'Bearer synthetic'}})).status).toBe(503);
  });

  it('preserves 404 and 405 canonical errors, including HEAD behavior', async () => {
    const app = createApi({config:config(),getPool:() => {throw new Error('unused');},requestId});
    expect((await app.request('/missing')).status).toBe(404);
    const wrong = await app.request('/v1/health', {method:'POST'});
    expect(wrong.status).toBe(405);
    expect((await wrong.json()).error).toEqual({code:'method_not_allowed',message:'Method not allowed',details:{},requestId:requestId()});
    expect((await app.request('/v1/health',{method:'HEAD'})).status).toBe(405);
  });
});

describe('production configuration', () => {
  it('uses pinned core VCTK metadata and real hosted capabilities, never synthetic fixture voices', () => {
    const c = loadConfig({KENKUI_ENV:'staging',KENKUI_ALLOWED_ORIGINS:'https://studio.example.invalid'});
    expect(c.catalog.voices).toHaveLength(59);
    expect(c.catalog.voices[0]).toEqual({id:'alasdair',name:'Alasdair',language:'english',licenseId:'CC-BY-4.0',voiceRights:'Derived from the VCTK corpus via kyutai/tts-voices. Review the VCTK terms and speaker consent for your intended use before commercial deployment.'});
    expect(c.catalog.voices.some(v => v.id === 'narrator')).toBe(false);
    expect(c.catalog.capabilities).toMatchObject({apiVersion:'1',auth:{mode:'session'},billing:{mode:'credits'},casting:{models:['openrouter/deepseek/deepseek-v4-flash'],modes:['single','characters']},sourceFormats:['epub']});
  });
  it('rejects missing environment and unknown voice selection rather than silently advertising another set', () => {
    expect(() => loadConfig({})).toThrow('KENKUI_ENV');
    expect(() => loadConfig({KENKUI_ENV:'production',KENKUI_VOICE_SET:'narrator'})).toThrow('manifest');
    expect(() => loadConfig({KENKUI_ENV:'staging',KENKUI_ALLOWED_ORIGINS:'*'})).toThrow('origin');
  });
  it('imports the production handler without reading required config or constructing a pool', async () => {
    const {default: handler} = await import('../src/index.js');
    expect(handler.fetch).toBeTypeOf('function');
  });
});
