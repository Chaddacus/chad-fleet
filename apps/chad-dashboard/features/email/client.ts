import type { EmailMessage, FleetStateResponse } from '@/lib/types';

/** Data access for the Email feature — read-fast list via the snapshot's `email` slice. */
export async function getEmail(): Promise<{ email: EmailMessage[]; error?: string }> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return { email: [], error: `HTTP ${res.status}` };
    const data = (await res.json()) as FleetStateResponse;
    return { email: data.email ?? [], error: data.error };
  } catch (err) {
    return { email: [], error: String(err) };
  }
}
