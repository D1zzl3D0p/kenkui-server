export const NATIVE_REDIRECT_URI='http://127.0.0.1:43827/callback';
export const AUTH_PROTOCOL_VERSION=2;
export interface NativeConfig {issuer:string;clientId:string;resource:string}
export interface NativeCredentials {accessToken:string;refreshToken:string;expiresAt:number;subject:string;refreshUncertain?:boolean}
/** Task10 binds OS secure storage + one app-wide/cross-process exclusive credential lock. */
export interface NativeCredentialStore {
  load():Promise<NativeCredentials|null>;save(value:NativeCredentials):Promise<void>;clear():Promise<void>;
  exclusive<T>(operation:()=>Promise<T>):Promise<T>;
}
type TokenVerifier=(token:string)=>Promise<{subject:string;expiresAt:number}>;
type Metadata={issuer:string;authorization_endpoint:string;token_endpoint:string;revocation_endpoint?:string};
const random=()=>base64url(crypto.getRandomValues(new Uint8Array(32)));
const base64url=(bytes:Uint8Array)=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');

/** Client-side protocol. Never instantiate on a shared API server for user credentials. */
export class NativeAuthClient {
  private attempt:{state:string;verifier:string;expiresAt:number}|undefined;
  private metadata:{value:Metadata;expiresAt:number}|undefined;
  private discovery:Promise<Metadata>|undefined;
  constructor(private config:NativeConfig,private storage:NativeCredentialStore,private verifyToken:TokenVerifier,private fetcher:typeof fetch=fetch) {
    const url=new URL(config.issuer),resource=new URL(config.resource);
    if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash||config.issuer.endsWith('/')||resource.protocol!=='https:'||!config.clientId) throw new Error('Invalid native auth configuration');
  }
  private async discover():Promise<Metadata> {
    if(this.metadata&&this.metadata.expiresAt>Date.now()) return this.metadata.value;
    if(this.discovery) return this.discovery;
    this.discovery=(async()=>{
      const response=await this.fetcher(this.config.issuer+'/.well-known/oauth-authorization-server',{redirect:'error',signal:AbortSignal.timeout(10_000)});
      if(!response.ok) throw new Error('Auth discovery unavailable');
      const data=await response.json() as Metadata;
      if(data.issuer!==this.config.issuer) throw new Error('Invalid OAuth issuer');
      for(const endpoint of [data.authorization_endpoint,data.token_endpoint,...(data.revocation_endpoint?[data.revocation_endpoint]:[])]) {
        const url=new URL(endpoint);if(!endpoint.startsWith(this.config.issuer+'/')||url.origin!==new URL(this.config.issuer).origin||url.search||url.hash||url.username||url.password) throw new Error('Invalid OAuth endpoint');
      }
      this.metadata={value:data,expiresAt:Date.now()+3_600_000};return data;
    })();
    try{return await this.discovery;}finally{this.discovery=undefined;}
  }
  /** Call only after the native adapter has bound the exact loopback listener. */
  async start():Promise<string> {
    this.attempt=undefined;
    const metadata=await this.discover(),state=random(),verifier=random();
    const challenge=base64url(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
    this.attempt={state,verifier,expiresAt:Date.now()+300_000};
    const url=new URL(metadata.authorization_endpoint);
    url.search=new URLSearchParams({response_type:'code',client_id:this.config.clientId,redirect_uri:NATIVE_REDIRECT_URI,scope:'openid profile email offline_access',state,code_challenge:challenge,code_challenge_method:'S256',resource:this.config.resource}).toString();
    return url.href;
  }
  async finish(callback:string):Promise<void> {
    const attempt=this.attempt;this.attempt=undefined;
    const url=new URL(callback),p=url.searchParams;
    if(!attempt||attempt.expiresAt<=Date.now()||url.origin+url.pathname!==NATIVE_REDIRECT_URI||url.username||url.password||url.hash||p.has('error')||p.getAll('state').length!==1||p.get('state')!==attempt.state||p.getAll('iss').length!==1||p.get('iss')!==this.config.issuer||p.getAll('code').length!==1||!p.get('code')) throw new Error('Invalid login callback');
    await this.storage.exclusive(async()=>{
      const credentials=await this.tokens({grant_type:'authorization_code',code:p.get('code')!,code_verifier:attempt.verifier,redirect_uri:NATIVE_REDIRECT_URI});
      await this.storage.save(credentials);
    });
  }
  private async tokens(fields:Record<string,string>,beforeRequest?:()=>Promise<void>):Promise<NativeCredentials> {
    const metadata=await this.discover();
    await beforeRequest?.();
    const response=await this.fetcher(metadata.token_endpoint,{method:'POST',redirect:'error',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({...fields,client_id:this.config.clientId,resource:this.config.resource}).toString(),signal:AbortSignal.timeout(15_000)});
    if(!response.ok) throw new Error('Sign in or retry authentication.');
    const data=await response.json() as Record<string,unknown>;
    if(typeof data.access_token!=='string'||data.access_token.split('.').length!==3||typeof data.refresh_token!=='string'||!data.refresh_token||String(data.token_type).toLowerCase()!=='bearer') throw new Error('Invalid token response');
    const verified=await this.verifyToken(data.access_token);
    if(!verified.subject||verified.expiresAt<=Date.now()/1000) throw new Error('Invalid access token');
    return {accessToken:data.access_token,refreshToken:data.refresh_token,expiresAt:verified.expiresAt,subject:verified.subject};
  }
  /** Refresh only before actual protected work/resume, under the credential owner's lock. */
  async getAccessToken():Promise<string> {
    return this.storage.exclusive(async()=>{
      const current=await this.storage.load();if(!current) throw new Error('Sign in to continue.');
      if(current.expiresAt>Date.now()/1000+60) return current.accessToken;
      if(current.refreshUncertain) throw new Error('Previous refresh outcome is unknown. Sign in again to safely renew access.');
      // Persist before sending: even an app crash must not replay a possibly rotated token.
      const next=await this.tokens({grant_type:'refresh_token',refresh_token:current.refreshToken},()=>this.storage.save({...current,refreshUncertain:true}));
      if(next.subject!==current.subject) throw new Error('Refresh identity mismatch');
      await this.storage.save(next);return next.accessToken;
    });
  }
  async logout():Promise<{revoked:boolean}> {
    this.attempt=undefined;
    return this.storage.exclusive(async()=>{
      let revoked=false;
      try {
        const current=await this.storage.load();if(!current) return {revoked:true};
        const metadata=await this.discover();
        if(metadata.revocation_endpoint) {
          const response=await this.fetcher(metadata.revocation_endpoint,{method:'POST',redirect:'error',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({client_id:this.config.clientId,token:current.refreshToken,token_type_hint:'refresh_token'}).toString(),signal:AbortSignal.timeout(15_000)});
          revoked=response.ok;
        }
      } catch {/* Local signout succeeds offline; never claim remote revocation. */}
      finally{await this.storage.clear();}
      return {revoked};
    });
  }
}
