import type { Pool } from 'pg';
import { requireUuid } from '../compute/modal.js';

/** Preferences are tied only to existing internal identities. Python retains send claims. */
export class NotificationStore {
  constructor(private readonly pool:()=>Pool){}
  async settings(owner:string):Promise<{email:string|null;emailOnCompletion:boolean}>{
    requireUuid(owner);
    const row=(await this.pool().query('SELECT email,notify_by_email FROM identities WHERE id=$1',[owner])).rows[0];
    if(!row)throw Error('unknown_identity');
    return {email:row.email,emailOnCompletion:row.notify_by_email};
  }
  async setEnabled(owner:string,enabled:boolean):Promise<void>{
    requireUuid(owner);
    await this.pool().query('UPDATE identities SET notify_by_email=$1 WHERE id=$2',[enabled,owner]);
  }
}
