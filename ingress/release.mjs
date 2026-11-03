import { readFile,writeFile,mkdir } from 'node:fs/promises';
const [environment,originValue]=process.argv.slice(2);
if(!['staging','production'].includes(environment))throw Error('Select staging or production');
const origin=new URL(originValue),host=environment==='staging'?'api.staging.kenkui.fm':'api.kenkui.fm';
if(origin.protocol!=='https:'||origin.username||origin.password||origin.search||origin.hash||origin.pathname!=='/'||origin.hostname===host)throw Error('Expected the exact reviewed upstream HTTPS origin, distinct from the public API');
// The source contains full-line comments only. No environment/secrets are copied.
const source=JSON.parse((await readFile('wrangler.jsonc','utf8')).split('\n').filter(line=>!line.trim().startsWith('//')).join('\n'));
source.main='../src/index.ts';source.env[environment].vars.ORIGIN_URL=origin.origin;
source.env[environment].routes=[{pattern:host,custom_domain:true}];
await mkdir('.wrangler',{recursive:true});
await writeFile(`.wrangler/release.${environment}.json`,JSON.stringify(source,null,2)+'\n');
console.log(`Prepared .wrangler/release.${environment}.json for review/dry-run; no provider changes.`);
