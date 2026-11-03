// Exact shipped runtime, verified TLS and synthetic data. No provider requests.
import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, readFile, writeFile, chmod, cp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:https';
import { generateKeyPair, exportJWK, SignJWT } from 'jose';
import { Client } from 'pg';
const exec=promisify(execFile),pg=process.env.KENKUI_PG_BIN;
assert(pg,'Set KENKUI_PG_BIN to the existing Postgres 17 binaries');
const root=await mkdtemp(join(tmpdir(),'kenkui-production-artifact-')),data=join(root,'pg'),cert=join(root,'cert.pem'),key=join(root,'key.pem');
let started=false,jwks;
try {
  assert.match((await exec(join(pg,'postgres'),['--version'])).stdout,/ 17\./);
  await exec('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1']);await chmod(key,0o600);
  await exec(join(pg,'initdb'),['-D',data,'--auth=trust','--username=postgres','--no-locale']);
  // Reserved for this disposable fixture only, distinct from shared 55438.
  await writeFile(join(data,'postgresql.conf'),`listen_addresses='127.0.0.1'\nport=55439\nssl=on\nssl_cert_file='${cert}'\nssl_key_file='${key}'\nunix_socket_directories='${root}'\n`);
  await exec(join(pg,'pg_ctl'),['-D',data,'-l',join(root,'postgres.log'),'-w','start']);started=true;
  const owner='11111111-1111-4111-8111-111111111111',subject='22222222-2222-4222-8222-222222222222';
  const {privateKey,publicKey}=await generateKeyPair('EdDSA'),jwk={...await exportJWK(publicKey),kid:'synthetic',alg:'EdDSA',use:'sig'};
  jwks=createServer({key:await readFile(key),cert:await readFile(cert)},(req,res)=>{assert.equal(req.url,'/db/auth/jwks');res.setHeader('content-type','application/json');res.end(JSON.stringify({keys:[jwk]}));});
  await new Promise(resolve=>jwks.listen(0,'127.0.0.1',resolve));const issuer=`https://127.0.0.1:${jwks.address().port}/db/auth`;
  const database='postgresql://postgres@127.0.0.1:55439/postgres',client=new Client({connectionString:database,ssl:{rejectUnauthorized:true,ca:await readFile(cert)}});await client.connect();
  try {await client.query('CREATE TABLE provider_identities(issuer TEXT,subject TEXT,identity_id UUID)');await client.query('INSERT INTO provider_identities VALUES ($1,$2,$3)',[issuer,subject,owner]);}finally{await client.end();}
  const token=await new SignJWT({client_id:'synthetic-public'}).setProtectedHeader({alg:'EdDSA',kid:'synthetic'}).setIssuer(issuer).setAudience('https://api.invalid').setSubject(subject).setIssuedAt().setExpirationTime('5m').sign(privateKey);
  await cp('dist',join(root,'artifact'),{recursive:true});
  await writeFile(join(root,'runner.mjs'),`import assert from 'node:assert/strict';
const headers={'x-kenkui-origin-secret':'synthetic-origin',authorization:'Bearer '+process.env.SYNTHETIC_JWT};
const {default:handler}=await import('./artifact/function/index.js');
assert.deepEqual(await (await handler.fetch(new Request('https://api.invalid/v1/auth/session',{headers}))).json(),{userId:'${owner}'});
const asset=await handler.fetch(new Request('https://api.invalid/v1/auth/client.js',{headers}));assert.equal(asset.status,200);assert((await asset.text()).length>1000);
const {serveApi}=await import('./artifact/standalone/standalone.js');const server=serveApi(undefined,{hostname:'127.0.0.1',port:0});await new Promise(r=>server.once('listening',r));
try {const response=await fetch('http://127.0.0.1:'+server.address().port+'/v1/auth/session',{headers});assert.equal(response.status,200);assert.deepEqual(await response.json(),{userId:'${owner}'});}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
console.log('exact production artifacts: Function + standalone verified-TLS PG/JWKS/JWT identity and embedded SDK passed');
`);
  await new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,[join(root,'runner.mjs')],{cwd:root,stdio:'inherit',env:{PATH:process.env.PATH,NODE_EXTRA_CA_CERTS:cert,KENKUI_ENV:'staging',KENKUI_ORIGIN_SECRET:'synthetic-origin',DATABASE_URL:database,NEON_AUTH_BASE_URL:issuer,NEON_AUTH_JWKS_URL:issuer+'/jwks',KENKUI_AUTH_RESOURCE:'https://api.invalid',KENKUI_NATIVE_CLIENT_ID:'synthetic-public',SYNTHETIC_JWT:token}});
    const deadline=setTimeout(()=>{child.kill('SIGKILL');reject(Error('Production artifact smoke timed out'));},30_000);
    child.once('error',reject);child.once('exit',code=>{clearTimeout(deadline);code===0?resolve():reject(Error('Production artifact smoke failed: '+code));});
  });
} finally {
  if(jwks){jwks.closeAllConnections();await new Promise(r=>jwks.close(r));}
  if(started)await exec(join(pg,'pg_ctl'),['-D',data,'-m','immediate','-w','stop']);
  await rm(root,{recursive:true,force:true});
}
