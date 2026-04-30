/**
 * Client-side fetch wrappers for the saved-view library.
 *
 * Hits the local Next.js proxy routes under /api/views which forward to the
 * view-registry service (default http://localhost:8108). This avoids CORS and
 * keeps the upstream URL out of the browser bundle.
 */

import type {
  SavedView,
  SavedViewListResponse,
  SavedViewResponse,
} from './types';

export interface CreateViewBody {
  name: string;
  prompt: string;
  description?: string;
  app_scope?: string[];
  tags?: string[];
}

/** GET /api/views — list all saved views. Pinned-first ordering. */
export async function fetchSavedViews(): Promise<SavedViewListResponse> {
  const res = await fetch('/api/views');
  if (!res.ok) return { items: [], error: `HTTP ${res.status}` };
  return res.json() as Promise<SavedViewListResponse>;
}

/** GET /api/views/[id] — fetch a single saved view. */
export async function fetchSavedView(id: string): Promise<SavedViewResponse> {
  const res = await fetch(`/api/views/${encodeURIComponent(id)}`);
  if (!res.ok) return { view: null, error: `HTTP ${res.status}` };
  const data = (await res.json()) as { view?: SavedView };
  return { view: data.view ?? null };
}

/** POST /api/views — create. Returns the created view or an error. */
export async function createSavedView(body: CreateViewBody): Promise<SavedViewResponse> {
  const res = await fetch('/api/views', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) return { view: null, error: `HTTP ${res.status}` };
  const data = (await res.json()) as { view?: SavedView };
  return { view: data.view ?? null };
}

/** DELETE /api/views/[id] — discard a saved view. */
export async function deleteSavedView(id: string): Promise<{ ok: boolean; error?: string }> {
  const res = await fetch(`/api/views/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
  return { ok: true };
}

/** POST /api/views/[id]/pin or /unpin. */
export async function togglePin(id: string, pinned: boolean): Promise<SavedViewResponse> {
  const verb = pinned ? 'unpin' : 'pin';
  const res = await fetch(`/api/views/${encodeURIComponent(id)}/${verb}`, { method: 'POST' });
  if (!res.ok) return { view: null, error: `HTTP ${res.status}` };
  const data = (await res.json()) as { view?: SavedView };
  return { view: data.view ?? null };
}
