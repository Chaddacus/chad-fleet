import type { FleetStateResponse, InboxResponse } from './types';

const GENUI_URL =
  typeof window !== 'undefined'
    ? (process.env.NEXT_PUBLIC_GENUI_URL ?? 'http://localhost:8107')
    : (process.env.NEXT_PUBLIC_GENUI_URL ?? 'http://localhost:8107');

/** Fetch fleet state via the local proxy route (avoids CORS). */
export async function fetchFleetState(): Promise<FleetStateResponse> {
  const res = await fetch('/api/state', { next: { revalidate: 5 } });
  if (!res.ok) {
    return { error: `HTTP ${res.status}`, apps: [], inbox_recent: [], summary: {}, generated_at: '' };
  }
  return res.json() as Promise<FleetStateResponse>;
}

/** Fetch inbox items via the local server-side API route. */
export async function fetchInbox(): Promise<InboxResponse> {
  const res = await fetch('/api/inbox');
  if (!res.ok) {
    return { error: `HTTP ${res.status}`, items: [] };
  }
  return res.json() as Promise<InboxResponse>;
}

/** Return the genui-renderer render endpoint URL.
 *
 * Direct cross-origin POST to genui-renderer:8107. The renderer's Express app
 * sets permissive CORS headers (see packages/genui-renderer/src/server.ts) so
 * the browser preflight + actual POST both succeed.
 */
export function getGenUiEndpoint(): string {
  return `${GENUI_URL}/render`;
}

/** Relative time helper — "3 minutes ago", "2 hours ago", etc. */
export function relativeTime(isoString: string): string {
  const date = new Date(isoString);
  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}
