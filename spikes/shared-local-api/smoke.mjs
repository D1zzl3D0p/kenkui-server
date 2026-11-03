import assert from 'node:assert/strict';
import { mkdtemp, copyFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { localExperiment } from './local.ts';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const directory=await mkdtemp(resolve(tmpdir(),'kenkui-boundary-'));
const source=resolve(directory,'book.epub'),state=resolve(directory,'state');
await copyFile(resolve(root,'../kenkui-studio/tests/fixtures/book.epub'),source);
const python=process.env.KENKUI_SERVER_PYTHON??resolve(root,'.venv/bin/python');
const started=performance.now();let worker=localExperiment(python,state,source);
try{
  await worker.ready;
  const startupMs=Math.round(performance.now()-started);
  const asset=await worker.call('upload');assert.equal(asset.status,201);
  const book=await worker.call('inspect',{sourceId:asset.result.id});assert.equal(book.status,200);assert.ok(book.result.chapters.length);
  const spec={sourceId:asset.result.id,chapters:[book.result.chapters[0].id],casting:{voiceId:'narrator'},output:{sourceCover:false}};
  const preflight=await worker.app.request('/v1/jobs/preflight',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(spec)});assert.equal(preflight.status,200);
  const job=await worker.call('start',spec);assert.equal(job.status,202);
  let snapshot;
  for(let i=0;i<100;i++){
    snapshot=await worker.call('snapshot',{jobId:job.result.id});
    if(['succeeded','failed','cancelled'].includes(snapshot.result.status))break;
    await new Promise(r=>setTimeout(r,100));
  }
  assert.equal(snapshot.result.status,'succeeded',JSON.stringify(snapshot));
  const events=await worker.call('events',{jobId:job.result.id});assert.equal(events.status,200);assert.match(events.result,/event:/);
  const cancelled=await worker.call('cancel',{jobId:job.result.id});assert.equal(cancelled.result.status,'succeeded');
  await assert.rejects(worker.call('preflight',{oversized:'x'.repeat(1048576)}),/Frame exceeds/);
  await worker.close();worker=localExperiment(python,state,source);await worker.ready;
  assert.equal((await worker.call('snapshot',{jobId:job.result.id})).result.status,'succeeded');
  worker.kill();await new Promise(r=>setTimeout(r,100));await assert.rejects(worker.call('inspect',{sourceId:asset.result.id}),/Worker exited/);
  console.log(JSON.stringify({startupMs,completedJobSurvivesRestart:true,protocolFrameLimit:true,workerCrashDetected:true,engine:'deterministic fixture, not real TTS',operations:['upload','inspect','preflight','start','snapshot','cancel','events'],decision:'adapter preserves a second full Python API; defer replacement'},null,2));
}finally{await worker.close();await rm(directory,{recursive:true,force:true});}
