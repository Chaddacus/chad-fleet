import type { FleetStateResponse, SessionSnapshot } from '@/lib/types';

/** Data access for the Sessions feature. Reads the unified snapshot's `sessions` slice. */
export async function getSessions(): Promise<{ sessions: SessionSnapshot[]; error?: string }> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return { sessions: [], error: `HTTP ${res.status}` };
    const data = (await res.json()) as FleetStateResponse;
    return { sessions: data.sessions ?? [], error: data.error };
  } catch (err) {
    return { sessions: [], error: String(err) };
  }
}
