import type { Pool } from 'pg';
import type { ContentfulStatusCode } from 'hono/utils/http-status';

/** A verified provider identity mapped to an existing internal Kenkui UUID. */
export interface Principal { userId: string; issuer: string; subject: string }
export interface AuthVerifier { verify(request: Request): Promise<Principal | null> }
export interface Voice { id: string; name: string; language: string | null; licenseId: string | null; voiceRights: string | null }
export interface Capabilities {
  apiVersion: '1'; auth: {mode:'session'|'bearer'}; billing: {mode:'credits'|'unmetered'};
  casting: {models:string[]; modes:string[]}; covers: {maxUploadBytes:number; read:boolean; upload:boolean};
  maxUploadBytes:number; narration:{charactersPerSecond:number; longChapterHours:number};
  notifications:{email:boolean}; outputFormats:string[]; sourceFormats:string[];
  pauseLengths:boolean; scenePauses:boolean; speechSettings:boolean;
}
export interface Catalog { capabilities: Capabilities; voices: readonly Voice[] }
export interface ApiConfig {
  environment: 'staging'|'production'; allowedOrigins: readonly string[]; catalog: Catalog;
  /** Server-only ingress credential; never a browser or native user credential. */
  ingressSecret?: string;
}
export interface LogEvent { event:string; method?:string; path?:string; requestId?:string; statusCode?:number }
export type Logger = (event: LogEvent) => void;
export interface ApiDeps {
  config: ApiConfig;
  getPool: () => Pool;
  /** Omission fails closed (503 for supplied credentials), until real auth is bound. */
  auth?: AuthVerifier;
  requestId?: () => string;
  log?: Logger;
}
export type ApiEnv = {Variables:{principal:Principal; requestId:string}};

/** Only expose safe client messages/details. Unexpected exceptions are sanitized. */
export class ApiError extends Error {
  constructor(
    public readonly status: ContentfulStatusCode,
    public readonly code: string,
    message: string,
    public readonly details: Record<string,unknown> = {},
    public readonly headers: Record<string,string> = {},
  ) { super(message); }
}
