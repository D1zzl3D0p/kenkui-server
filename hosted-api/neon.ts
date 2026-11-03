import { defineConfig } from '@neon/config/v1';

// Declaration only. Applying/deploying this file is an explicit later release step.
export default defineConfig({functions:{kenkui:{name:'Kenkui API',source:'./src/index.ts'}}});
