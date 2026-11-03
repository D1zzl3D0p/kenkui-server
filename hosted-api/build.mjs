import { build } from 'esbuild';

const browser=await build({entryPoints:['src/auth/page-client.ts'],bundle:true,platform:'browser',target:['es2022'],format:'iife',write:false,minify:true});
await build({entryPoints:['src/index.ts'],bundle:true,platform:'node',target:'node24',format:'esm',packages:'external',sourcemap:'external',sourcesContent:false,outfile:'dist/index.js',define:{AUTH_BROWSER_SOURCE:JSON.stringify(browser.outputFiles[0].text)}});
