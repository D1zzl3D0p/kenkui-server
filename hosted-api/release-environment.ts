/** Deploy-time allowlist. The provider's automatically injected owner URL is never used. */
export function releaseEnvironment(env:NodeJS.ProcessEnv):Record<string,string> {
  const required=(name:string)=>{const value=env[name];if(!value?.trim())throw Error('Missing release configuration: '+name);return value;};
  const environment=required('KENKUI_ENV');
  if(environment!=='staging'&&environment!=='production')throw Error('Invalid release environment');
  const database=required('KENKUI_API_DATABASE_URL');
  const pin=JSON.parse(required('KENKUI_RELEASE_AUTHORITY')) as Record<string,unknown>;
  const origin=environment==='staging'?'https://api.staging.kenkui.fm':'https://api.kenkui.fm';
  if(!pin||Object.keys(pin).sort().join(',')!=='apiOrigin,clientId,issuer,jwksUrl,resource'||pin.apiOrigin!==origin||pin.resource!==origin||typeof pin.clientId!=='string'||!pin.clientId.trim())throw Error('Release authority mismatch');
  for(const value of [pin.issuer,pin.jwksUrl]) {
    if(typeof value!=='string')throw Error('Invalid release authority');
    const url=new URL(value);if(url.protocol!=='https:'||url.username||url.password||url.href!==value||url.search||url.hash||url.pathname.endsWith('/'))throw Error('Invalid release authority');
  }
  const selected:Record<string,string>={KENKUI_ENV:environment,DATABASE_URL:database,KENKUI_INGRESS_PROTECTION:'true',NEON_AUTH_BASE_URL:pin.issuer as string,NEON_AUTH_JWKS_URL:pin.jwksUrl as string,KENKUI_AUTH_RESOURCE:origin,KENKUI_NATIVE_CLIENT_ID:pin.clientId};
  for(const name of ['KENKUI_ALLOWED_ORIGINS','KENKUI_ORIGIN_SECRET','R2_ENDPOINT','R2_BUCKET','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','KENKUI_MODAL_BRIDGE_URL','KENKUI_MODAL_PROXY_KEY','KENKUI_MODAL_PROXY_SECRET','KENKUI_BRIDGE_SECRET','KENKUI_UNSUBSCRIBE_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET','KENKUI_WEB_ORIGIN','KENKUI_STRIPE_API_VERSION'])selected[name]=required(name);
  if(env.R2_KEY_SALT===undefined)throw Error('Missing release configuration: R2_KEY_SALT');
  selected.R2_KEY_SALT=env.R2_KEY_SALT; // Existing empty-salt keys must remain addressable.
  for(const name of ['KENKUI_EMAIL_NOTIFICATIONS','KENKUI_MAX_SPEECH_CHARACTERS','KENKUI_VOICE_SET','KENKUI_MODEL_ALLOWLIST','KENKUI_LEGACY_TOKEN_ISSUER','KENKUI_STRIPE_TAX_CODE'])if(env[name])selected[name]=env[name]!;
  return selected;
}
