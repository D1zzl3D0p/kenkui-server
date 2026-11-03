import { mkdtemp,mkdir,writeFile,rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync,spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect,it } from 'vitest';

const pin={apiOrigin:'https://api.staging.kenkui.fm',issuer:'https://auth.invalid/db/auth',jwksUrl:'https://auth.invalid/db/auth/jwks',clientId:'public',resource:'https://api.staging.kenkui.fm'};
const names=['function/index.js','function/package.json','standalone/standalone.js','standalone/package.json'];
const required=['KENKUI_ALLOWED_ORIGINS','KENKUI_ORIGIN_SECRET','R2_ENDPOINT','R2_BUCKET','R2_KEY_SALT','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','KENKUI_MODAL_BRIDGE_URL','KENKUI_MODAL_PROXY_KEY','KENKUI_MODAL_PROXY_SECRET','KENKUI_BRIDGE_SECRET','KENKUI_UNSUBSCRIBE_SECRET','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET','KENKUI_WEB_ORIGIN','KENKUI_STRIPE_API_VERSION'];
async function fixture() {
  await mkdir('dist',{recursive:true});const directory=await mkdtemp(resolve('dist/receipt-test-')),files=[];
  for(const name of names) {const value=Buffer.from('reviewed '+name),path=resolve(directory,'dist',name);await mkdir(resolve(path,'..'),{recursive:true});await writeFile(path,value);files.push({path:name,sha256:createHash('sha256').update(value).digest('hex')});}
  const manifest={formatVersion:1,environment:'staging',serverSha:execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(),studioSha:'167154028908dbd90a9d42f3da71f7e9b2d46b7b',coreSha:'97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c',node:'24.21.0',authority:pin,files};
  await writeFile(resolve(directory,'dist/release-manifest.json'),JSON.stringify(manifest));return directory;
}
function config(directory:string,extra:Record<string,string>={}) {
  const script=`const {default:config}=await import(${JSON.stringify(new URL('../neon.ts',import.meta.url).href)});console.log(JSON.stringify(config.functions.kenkui.env));`;
  return spawnSync(process.execPath,['--input-type=module','-e',script],{cwd:directory,encoding:'utf8',env:{PATH:process.env.PATH??'',...Object.fromEntries(required.map(name=>[name,'synthetic'])),KENKUI_ENV:'staging',KENKUI_API_DATABASE_URL:'postgresql://api_runtime@synthetic.invalid/db',KENKUI_ESTIMATED_COST_CENTS_PER_MILLION:'333',...extra}});
}
it('actual canonical deploy config derives all pins from verified receipt/runtime hashes',async()=>{
  const directory=await fixture();try{
    const result=config(directory,{NEON_AUTH_JWKS_URL:'https://ignored.invalid/keys'});expect(result.status).toBe(0);
    const env=JSON.parse(result.stdout);expect(env).toMatchObject({NEON_AUTH_BASE_URL:pin.issuer,NEON_AUTH_JWKS_URL:pin.jwksUrl,KENKUI_NATIVE_CLIENT_ID:pin.clientId,KENKUI_ESTIMATED_COST_CENTS_PER_MILLION:'333'});
  }finally{await rm(directory,{recursive:true,force:true});}
});
it('actual deploy path rejects a JWKS-only env substitution before configuration can apply',async()=>{
  const directory=await fixture();try{
    const result=config(directory,{KENKUI_RELEASE_AUTHORITY:JSON.stringify({...pin,jwksUrl:'https://substituted.invalid/jwks'})});expect(result.status).not.toBe(0);expect(result.stderr).toContain('Release authority override mismatch');
  }finally{await rm(directory,{recursive:true,force:true});}
});
it('actual deploy path refuses runtime bytes altered after a verified restore',async()=>{
  const directory=await fixture();try{
    await writeFile(resolve(directory,'dist/function/index.js'),'tampered');const result=config(directory);expect(result.status).not.toBe(0);expect(result.stderr).toContain('Release runtime checksum mismatch');
  }finally{await rm(directory,{recursive:true,force:true});}
});
