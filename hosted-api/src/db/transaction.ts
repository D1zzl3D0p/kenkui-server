import type { Pool, PoolClient } from 'pg';

export async function transaction<T>(pool:Pool,operation:(client:PoolClient)=>Promise<T>):Promise<T>{
  const client=await pool.connect();
  let discard=false;
  try {
    await client.query('BEGIN');
    const result=await operation(client);
    await client.query('COMMIT');
    return result;
  } catch(error) {
    try {await client.query('ROLLBACK');} catch {discard=true;}
    throw error;
  } finally {client.release(discard);}
}
