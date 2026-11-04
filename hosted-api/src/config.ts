import bundledCatalog from './catalog.json' with {type:'json'};
import type { ApiConfig, Voice } from './types.js';

export const CORE_SHA = '97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c';
const DEFAULT_MODEL = 'openrouter/deepseek/deepseek-v4-flash';

/** Parse only explicitly passed environment state; never run at module import. */
export function loadConfig(env: NodeJS.ProcessEnv): ApiConfig {
  if (env.KENKUI_ENV !== 'staging' && env.KENKUI_ENV !== 'production') throw new Error('KENKUI_ENV must be staging or production');
  const environment = env.KENKUI_SERVICE_ENVIRONMENT ?? env.KENKUI_ENV;
  if (environment !== 'staging' && environment !== 'production') throw new Error('Invalid service environment');
  if (env.KENKUI_ACCOUNT_POLICY !== undefined && !['migration','neon'].includes(env.KENKUI_ACCOUNT_POLICY)) throw new Error('Invalid account policy');
  const allowedOrigins = (env.KENKUI_ALLOWED_ORIGINS ?? '').split(',').map(x => x.trim()).filter(Boolean);
  for (const origin of allowedOrigins) {
    let parsed:URL;
    try {parsed = new URL(origin);} catch {throw new Error('Invalid allowed origin');}
    if (!['https:','http:'].includes(parsed.protocol) || parsed.origin !== origin || parsed.username || parsed.password) throw new Error('Invalid allowed origin');
  }
  if (bundledCatalog.coreSha !== CORE_SHA || bundledCatalog.selector !== 'vctk' || !bundledCatalog.voices.length) throw new Error('A pinned core catalog manifest is required');
  const selector = (env.KENKUI_VOICE_SET ?? 'vctk').trim();
  const ids = new Set(selector.split(',').map(x => x.trim()).filter(Boolean));
  const voices:Voice[] = selector.toLowerCase() === 'vctk' ? bundledCatalog.voices : bundledCatalog.voices.filter(v => ids.has(v.id));
  if (!voices.length || (selector.toLowerCase() !== 'vctk' && voices.length !== ids.size)) throw new Error('Selected voices are unavailable in the pinned core catalog manifest');
  const models = (env.KENKUI_MODEL_ALLOWLIST?.trim() || DEFAULT_MODEL).split(',').map(x => x.trim()).filter(Boolean);
  const ingressSecret = env.KENKUI_ORIGIN_SECRET;
  if (env.KENKUI_INGRESS_PROTECTION === 'true' && !ingressSecret) throw new Error('Ingress protection requires KENKUI_ORIGIN_SECRET');
  return {
    environment, allowedOrigins,
    ...(ingressSecret ? {ingressSecret} : {}),
    catalog:{voices,capabilities:{
      apiVersion:'1',auth:{mode:'session'},billing:{mode:'credits'},
      casting:{models,modes:models.length ? ['single','characters'] : ['single']},
      covers:{maxUploadBytes:8*1024*1024,read:true,upload:true},
      maxUploadBytes:50*1024*1024,narration:{charactersPerSecond:13,longChapterHours:6},
      notifications:{email:env.KENKUI_EMAIL_NOTIFICATIONS === 'true'},
      outputFormats:['m4b'],sourceFormats:['epub'],pauseLengths:true,scenePauses:true,speechSettings:true,
    }},
  };
}
