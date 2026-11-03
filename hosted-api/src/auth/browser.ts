import { createAuthClient } from '@neon/auth';
import { BetterAuthVanillaAdapter } from '@neon/auth/vanilla/adapters';

type Result<T>={data:T|null;error?:unknown};
interface BrowserSdk {
  getSession():Promise<Result<{session:unknown}>>;
  token():Promise<Result<{token?:string;session?:{token?:string}}>>;
  signIn:{social(input:{provider:'google'|'github';callbackURL?:string}):Promise<unknown>};
  signOut():Promise<unknown>;
  oauth2:{continue(input:{selected:true}):Promise<unknown>;consent(input:{accept:boolean}):Promise<unknown>;publicClient(input:{query:{client_id:string}}):Promise<unknown>};
}
function managedClient(issuer:string) {
  return createAuthClient(issuer,{allowAnonymous:false,adapter:BetterAuthVanillaAdapter({
    fetchOptions:{credentials:'include'},
    sessionOptions:{refetchInterval:0,refetchOnWindowFocus:false,refetchWhenOffline:false},
  })});
}
function checked<T>(result:T):T {
  if(result&&typeof result==='object'&&'error' in result&&result.error) throw new Error('Authentication failed. Please try signing in again.');
  return result;
}
/** No useSession atom/hook subscriptions, including while hidden. Task10 uses this facade. */
export function createBrowserAuth(issuer:string,visible:()=>boolean=()=>document.visibilityState==='visible',sdk:BrowserSdk=managedClient(issuer)) {
  let pending:Promise<unknown>|undefined;
  async function resume() {
    if(!visible()) return null;
    if(pending) return pending;
    pending=sdk.getSession().then(checked);
    try{return await pending;}finally{pending=undefined;}
  }
  return {
    resume,
    async accessToken():Promise<string> {
      if(!visible()) throw new Error('Resume the app before making a protected request.');
      const result=checked(await sdk.token());
      // 0.8.2's request cache serves /token using the cached getSession shape.
      const token=result.data?.token??result.data?.session?.token;
      if(!token) throw new Error('Sign in to continue.');return token;
    },
    async login(provider:'google'|'github',callbackURL?:string) {
      if(provider!=='google'&&provider!=='github') throw new Error('Unsupported login provider');
      checked(await sdk.signIn.social({provider,...(callbackURL?{callbackURL}:{})}));
    },
    async continueLogin() {
      if(!visible()) return;
      const result=checked(await sdk.getSession());
      if(result.data?.session) checked(await sdk.oauth2.continue({selected:true}));
    },
    async clientDetails(clientId:string) {return checked(await sdk.oauth2.publicClient({query:{client_id:clientId}}));},
    async consent(accept:boolean) {checked(await sdk.oauth2.consent({accept}));},
    async signOut() {checked(await sdk.signOut());},
  };
}
