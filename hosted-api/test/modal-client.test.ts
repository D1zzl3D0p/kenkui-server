import { describe, expect, it, vi } from 'vitest';
import { ModalBridge } from '../src/compute/modal.js';

const owner = '11111111-1111-4111-8111-111111111111';
const source = '22222222-2222-4222-8222-222222222222';
const config = {url:'https://synthetic--api-bridge.modal.run',environment:'staging' as const,
  key:'synthetic-key',secret:'synthetic-proxy-secret',bridgeSecret:'synthetic-inner-secret'};
const book = {sourceId:source,title:'Tiny',author:'Ada',chapters:[{id:'one',title:'One',speechCharacters:16}]};

describe('authenticated bounded Modal client', () => {
  it('pins all three credentials and derives ownership/environment, with no redirects', async () => {
    const transport = vi.fn(async (_input:unknown,_init?:RequestInit) => Response.json(book));
    const bridge = new ModalBridge(config,transport);
    expect(await bridge.inspect(owner,source)).toEqual(book);
    const [url,init] = transport.mock.calls[0]!;
    expect(url).toBe(config.url+'/inspect');
    expect(init?.redirect).toBe('error');
    expect(new Headers(init?.headers).get('Modal-Key')).toBe(config.key);
    expect(new Headers(init?.headers).get('Modal-Secret')).toBe(config.secret);
    expect(new Headers(init?.headers).get('X-Kenkui-Bridge-Secret')).toBe(config.bridgeSecret);
    expect(JSON.parse(String(init?.body))).toEqual({environment:'staging',ownerId:owner,sourceId:source});
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });
  it('keeps duplicate cover requests semantically identical and dispatch free of identity assertions', async () => {
    const asset={id:source,format:'epub',sha256:'a'.repeat(64)};
    const transport=vi.fn(async (url:unknown,_init?:RequestInit) => Response.json(String(url).endsWith('/dispatch')?{accepted:true}:asset));
    const bridge=new ModalBridge(config,transport);
    const cover=Buffer.from('89504e470d0a1a0a','hex');
    await bridge.replaceCover(owner,source,cover,'image/png');
    await bridge.replaceCover(owner,source,cover,'image/png');
    expect(transport.mock.calls[0]?.[0]).toBe(transport.mock.calls[1]?.[0]);
    expect(transport.mock.calls[0]?.[1]?.body).toBe(transport.mock.calls[1]?.[1]?.body);
    await bridge.dispatch(source);
    const calls=transport.mock.calls as unknown as Array<[string,RequestInit]>;
    expect(JSON.parse(String(calls[2]![1].body))).toEqual({environment:'staging',dispatchId:source});
  });
  it.each([429,503,504])('sanitizes transient %s without retrying ambiguous operations', async status => {
    const transport=vi.fn(async()=>new Response('private provider credentials',{status,headers:{'Retry-After':'999999'}}));
    await expect(new ModalBridge(config,transport).inspect(owner,source)).rejects.toMatchObject({status:status===429?429:503,code:'compute_unavailable'});
    expect(transport).toHaveBeenCalledTimes(1);
  });
  it('preserves the public stale-inspection message without forwarding upstream details',async()=>{
    const bridge=new ModalBridge(config,async()=>new Response('internal SQL/path',{status:409}));
    await expect(bridge.inspect(owner,source)).rejects.toMatchObject({status:409,message:"This book's chapter layout has changed since it was uploaded. Upload the book again and choose chapters from the updated list."});
  });
  it('bounds its wait even if transport ignores cancellation', async () => {
    const bridge=new ModalBridge({...config,timeoutMs:5},async()=>new Promise(()=>{}));
    await expect(bridge.inspect(owner,source)).rejects.toMatchObject({status:503,code:'compute_unavailable'});
  });
  it('rejects foreign response identities, oversized responses and untrusted endpoints', async () => {
    await expect(new ModalBridge(config,async()=>Response.json({...book,sourceId:owner})).inspect(owner,source)).rejects.toMatchObject({status:502});
    await expect(new ModalBridge(config,async()=>new Response('x',{headers:{'Content-Length':'999999999'}})).inspect(owner,source)).rejects.toMatchObject({status:502});
    for(const url of ['http://synthetic.modal.run','https://evil.invalid','https://synthetic.modal.run/path','https://user:pass@synthetic.modal.run']) {
      expect(()=>new ModalBridge({...config,url})).toThrow();
    }
  });
});
