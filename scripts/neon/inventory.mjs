import { createHash } from 'node:crypto';
import { execFileSync,spawnSync } from 'node:child_process';
import { lstat,realpath,readFile,writeFile } from 'node:fs/promises';
import { dirname,basename,resolve,join } from 'node:path';
import { fileURLToPath,pathToFileURL } from 'node:url';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const bodyKeys=['types','databaseCollation','tables','schemaObjects','sequences','migrationIds','creditInvariants','r2','sourceProjection','restoredSnapshotDigest'];
const provenanceKeys=['format','environment','target','mode','capturedAt','managedAuth','sourceProjectionCaptureDigest','fullDigest'];
function canonical(value) {
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value!==null&&typeof value==='object')return '{'+Object.keys(value).sort((a,b)=>Buffer.compare(Buffer.from(a),Buffer.from(b))).map(k=>canonical(k)+':'+canonical(value[k])).join(',')+'}';
  if(typeof value==='number'&&!Number.isSafeInteger(value))throw Error('Unsafe inventory number');
  const encoded=JSON.stringify(value);if(encoded===undefined)throw Error('Incomplete inventory');
  return encoded.replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
}
function hashKeys(manifest,keys) {return createHash('sha256').update(canonical(Object.fromEntries(keys.map(k=>[k,manifest[k]]))),'ascii').digest('hex');}
export function inventoryDigest(manifest) {return hashKeys(manifest,bodyKeys);}
export function captureDigest(manifest) {return hashKeys(manifest,provenanceKeys);}
function python(options) {
  return JSON.parse(execFileSync(resolve(root,'.venv/bin/python'),[resolve(root,'scripts/neon/inventory.py')],{cwd:root,input:JSON.stringify(options),encoding:'utf8',timeout:300_000,maxBuffer:64*1024*1024,stdio:['pipe','pipe','pipe'],env:{PATH:process.env.PATH,PYTHONPATH:resolve(root,'src')}}));
}
export function checkedOptions(options) {
  try {
    python({...options,action:'validate'});
    if(options.sourceProjection&&(options.sourceProjection.format!=='kenkui-restore-inventory-v2'||options.sourceProjection.environment!==options.environment||captureDigest(options.sourceProjection)!==options.sourceProjection.captureDigest||inventoryDigest(options.sourceProjection)!==options.sourceProjection.fullDigest))throw Error();
    return options;
  } catch {throw Error('Invalid private inventory target/connection/TLS configuration');}
}
export async function captureInventory(options) {
  checkedOptions(options);
  try {return python({...options,action:'capture'});}catch {throw Error(options.emptyOnly?'Restore target occupied or unreadable; no changes made':'Inventory capture blocked by data, privilege, RLS or schema check');}
}
export async function readPrivateJson(path) {
  const info=await lstat(path);
  if(!info.isFile()||info.isSymbolicLink()||info.uid!==process.getuid()||(info.mode&0o077)!==0||info.size>64*1024*1024)throw Error('Private owned regular mode0600 input required');
  return JSON.parse(await readFile(path,'utf8'));
}
export async function writePrivateManifest(path,manifest) {
  const failure=()=>Error('Private evidence placement must be positively verified outside Git');
  const parent=await realpath(dirname(resolve(path))),info=await lstat(parent);
  if(!info.isDirectory()||info.uid!==process.getuid()||(info.mode&0o077)!==0)throw failure();
  for(let dir=parent;;dir=dirname(dir)) {
    if(basename(dir)==='.git')throw failure();
    try {await lstat(join(dir,'.git'));throw failure();}
    catch(error) {if(error.code!=='ENOENT')throw failure();}
    if(dirname(dir)===dir)break;
  }
  const git=spawnSync('git',['-C',parent,'rev-parse','--is-inside-work-tree','--is-inside-git-dir'],{encoding:'utf8',env:{PATH:process.env.PATH,LANG:'C',LC_ALL:'C',GIT_CONFIG_NOSYSTEM:'1',GIT_CONFIG_GLOBAL:'/dev/null'},stdio:['ignore','pipe','pipe']});
  const outside=git.status===128&&!git.error&&git.stderr.trim()==='fatal: not a git repository (or any of the parent directories): .git';
  if(!outside)throw failure();
  await writeFile(join(parent,basename(path)),JSON.stringify(manifest,null,2)+'\n',{flag:'wx',mode:0o600});
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  try {
    const [action,configPath,output,...extra]=process.argv.slice(2);
    if(action!=='capture'||!configPath||!output||extra.length)throw Error();
    const config=await readPrivateJson(configPath);
    if(config.sourceProjectionPath)config.sourceProjection=await readPrivateJson(config.sourceProjectionPath);
    const manifest=await captureInventory({...config,databaseUrl:process.env.KENKUI_INVENTORY_DATABASE_URL});
    await writePrivateManifest(output,manifest);
    console.log(JSON.stringify({tables:Object.keys(manifest.tables).length,fullDigest:manifest.fullDigest,captureDigest:manifest.captureDigest,restoredSnapshotDigest:manifest.restoredSnapshotDigest}));
  } catch {console.error('Inventory capture failed; inspect private configuration and permissions.');process.exitCode=1;}
}
