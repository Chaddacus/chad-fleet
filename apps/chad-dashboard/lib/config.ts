/* Centralized hub configuration — the single place env names are read.
 *
 * This is the code side of the env contract (Codex review #6). Service URLs follow the
 * repo's existing `NEXT_PUBLIC_<X>_URL` convention. Auth secrets are server-only (NO
 * NEXT_PUBLIC prefix) so they never reach the browser bundle; they are still readable in
 * middleware and route handlers, which run server-side.
 */

export const config = {
  // --- service URLs (client-exposable) ---
  admiralUrl: process.env.NEXT_PUBLIC_ADMIRAL_URL ?? 'http://localhost:8901',
  aggregatorUrl: process.env.NEXT_PUBLIC_AGGREGATOR_URL ?? 'http://localhost:8106',
  genuiUrl: process.env.NEXT_PUBLIC_GENUI_URL ?? 'http://localhost:8107',
  appUrl: process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000',

  // --- auth (server-only secrets) ---
  authSecret: process.env.HUB_AUTH_SECRET ?? '',
  // scrypt$<saltHex>$<hashHex>; when empty the gate is OPEN (single-user local dev).
  authPasswordHash: process.env.HUB_AUTH_PASSWORD_HASH ?? '',
  // session lifetime in seconds (default 7 days)
  authTtlSeconds: Number(process.env.HUB_AUTH_TTL_SECONDS ?? 60 * 60 * 24 * 7),
} as const;

/** The gate is enforced only when a password hash is configured. Unset => open dev mode. */
export function authEnabled(): boolean {
  return config.authPasswordHash.length > 0;
}

/** Effective signing secret. Falls back to a dev-only constant (warned) when unset. */
export function signingSecret(): string {
  if (config.authSecret) return config.authSecret;
  return 'dev-insecure-secret-set-HUB_AUTH_SECRET-in-production';
}

export const SESSION_COOKIE = 'hub_session';
