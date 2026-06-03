/* Route-gating decision — Edge-safe (imported by middleware.ts).
 *
 * Single responsibility: given a request, is it allowed through? The middleware delegates
 * the entire policy here so the shell stays dumb.
 */

import type { NextRequest } from 'next/server';
import { SESSION_COOKIE, authEnabled, config, signingSecret } from '@/lib/config';
import { verifySession } from './session';

/** Paths reachable without a session: the login page and the auth API. */
const PUBLIC_PREFIXES = ['/login', '/api/auth/'];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(p));
}

/** True when the request carries a valid session (or auth is disabled / dev-open). */
export async function isAuthed(req: NextRequest): Promise<boolean> {
  if (!authEnabled()) return true; // open dev mode — no password hash configured
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return false;
  return verifySession(token, signingSecret());
}

export { SESSION_COOKIE, config };
