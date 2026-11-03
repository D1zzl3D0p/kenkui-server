import type { Pool } from 'pg';
import { streamQuery } from './stream-query.js';

export const eventBatchSize=100;
export interface JobEvent {sequence:bigint;type:string;progressJson:string}
// The Python migrations own the INTEGER sequence column and its composite primary key.
const maxSequence=2147483647n;
export const eventQuery=`SELECT sequence::text,event_type,progress_json FROM job_events
  WHERE job_id=$1 AND sequence>$2 ORDER BY job_events.sequence LIMIT $3`;

export class EventStore {
  constructor(private readonly pool:()=>Pool){}
  async readEvents(jobId:string,afterSequence:bigint,limit:number,signal?:AbortSignal):Promise<JobEvent[]> {
    if(!Number.isSafeInteger(limit)||limit<1||limit>eventBatchSize)throw Error('Invalid event batch size');
    // Out-of-domain Python integers mean no matching rows, not a PostgreSQL int4 error.
    const cursor=afterSequence<0n?0n:afterSequence>maxSequence?maxSequence:afterSequence;
    const rows=await streamQuery<{sequence:string;event_type:string;progress_json:string}>(this.pool,eventQuery,[jobId,cursor.toString(),limit],signal);
    return rows.map(row=>({sequence:BigInt(row.sequence),type:row.event_type,progressJson:row.progress_json}));
  }
  async readStatus(jobId:string,signal?:AbortSignal):Promise<string> {
    const rows=await streamQuery<{status:string}>(this.pool,'SELECT status FROM jobs WHERE id=$1',[jobId],signal);
    if(!rows[0])throw Error('Missing streamed job');
    return rows[0].status;
  }
}
