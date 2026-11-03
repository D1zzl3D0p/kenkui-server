import { createBrowserAuth } from './browser.js';
import { NATIVE_REDIRECT_URI } from './native.js';

const body=document.body,status=document.querySelector<HTMLElement>('#status')!;
const auth=createBrowserAuth(body.dataset.issuer!);
const report=(error:unknown)=>{status.textContent=error instanceof Error?error.message:'Authentication failed. Please try again.';};
const run=(work:()=>Promise<unknown>)=>{void work().catch(report);};
for(const button of document.querySelectorAll<HTMLButtonElement>('[data-provider]')) button.addEventListener('click',()=>run(()=>auth.login(button.dataset.provider as 'google'|'github',location.href)));
document.querySelector('#signout')?.addEventListener('click',()=>run(async()=>{await auth.signOut();status.textContent='Signed out of this browser.';}));
async function load() {
  if(document.visibilityState!=='visible') return;
  if(body.dataset.page==='login') {
    const query=new URLSearchParams(location.search);
    if(query.has('sig')&&query.has('ba_param')) await auth.continueLogin();
    else {await auth.resume();status.textContent='After signing in, return to Kenkui.';}
    return;
  }
  const query=new URLSearchParams(location.search);
  // Display only the owned client's exact request. SDK passes the original signed query.
  if(['client_id','redirect_uri','resource','scope'].some(key=>query.getAll(key).length!==1)||query.get('client_id')!==body.dataset.clientId||query.get('redirect_uri')!==NATIVE_REDIRECT_URI||query.get('resource')!==body.dataset.resource) throw new Error('Invalid authorization request. Start sign-in from Kenkui Desktop.');
  const result=await auth.clientDetails(body.dataset.clientId!) as {data?:{client_name?:string}};
  document.querySelector('#client')!.textContent='App: '+(result.data?.client_name??'Kenkui Desktop');
  document.querySelector('#scope')!.textContent='Permissions: '+(query.get('scope')??'');
  document.querySelector('#resource')!.textContent='API: '+body.dataset.resource;
  document.querySelector('#callback')!.textContent='Callback: '+NATIVE_REDIRECT_URI;
  for(const id of ['accept','deny']) (document.querySelector('#'+id) as HTMLButtonElement).disabled=false;
}
for(const id of ['accept','deny']) document.querySelector('#'+id)?.addEventListener('click',()=>run(()=>auth.consent(id==='accept')));
// No online listeners, SDK atom subscriptions, periodic session reads or token refresh.
let loading:Promise<void>|undefined;
const resume=()=>{if(!loading) {loading=load().catch(report).finally(()=>{loading=undefined;});}};
document.addEventListener('visibilitychange',resume);
resume();
