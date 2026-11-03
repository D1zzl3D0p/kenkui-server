import { build } from 'esbuild';
import { builtinModules } from 'node:module';
import { mkdir, rm, writeFile } from 'node:fs/promises';

const builtins=new Set(builtinModules.flatMap(name=>[name,'node:'+name]));
const browser=await build({entryPoints:['src/auth/page-client.ts'],bundle:true,platform:'browser',target:['es2022'],format:'iife',write:false,minify:true});
export async function buildRuntime(entry,outfile) {
  const result=await build({entryPoints:[entry],bundle:true,platform:'node',target:'node24',format:'esm',metafile:true,
    // pg-native is an optional accelerator. The API uses ordinary pg.Pool only.
    external:['pg-native'],banner:{js:"import { createRequire as __createRequire } from 'node:module'; const require = __createRequire(import.meta.url);"},
    outfile,define:{AUTH_BROWSER_SOURCE:JSON.stringify(browser.outputFiles[0].text),STANDALONE_ENTRY:JSON.stringify(entry==='src/standalone.ts')}});
  for(const output of Object.values(result.metafile.outputs))for(const item of output.imports) {
    if(item.external && !builtins.has(item.path) && item.path!=='pg-native')throw Error('Unpackaged runtime import: '+item.path);
  }
}
if(import.meta.main) {
  // Construct from a fixed allowlist, never copy the source tree or caller env.
  await rm('dist',{recursive:true,force:true});
  for(const [directory,entry,filename] of [['function','src/index.ts','index.js'],['standalone','src/standalone.ts','standalone.js']]) {
    await mkdir('dist/'+directory,{recursive:true});
    await buildRuntime(entry,'dist/'+directory+'/'+filename);
    await writeFile('dist/'+directory+'/package.json',JSON.stringify({private:true,type:'module',engines:{node:'24.21.0'}})+'\n');
  }
}
