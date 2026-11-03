import { defineConfig } from '@neon/config/v1';
import { releaseEnvironment } from './release-environment.ts';
import { reviewedRelease } from './release-manifest.ts';

// Declaration only. Applying/deploying this file is an explicit later release step.
// Build first: dist embeds the pinned Managed SDK browser asset. Provider policy is
// configured per branch using deploy/neon/oauth-setup.md before admitting users.
export default defineConfig({auth:true,functions:{kenkui:{name:'Kenkui API',source:'./dist/function',bundler:'none',env:releaseEnvironment(process.env,reviewedRelease(process.env.KENKUI_ENV).authority)}}});
