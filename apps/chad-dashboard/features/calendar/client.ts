import type { CalendarEvent, FleetStateResponse } from '@/lib/types';

/** Data access for the Calendar feature — read-fast list via the snapshot's `calendar` slice. */
export async function getCalendar(): Promise<{ calendar: CalendarEvent[]; error?: string }> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return { calendar: [], error: `HTTP ${res.status}` };
    const data = (await res.json()) as FleetStateResponse;
    return { calendar: data.calendar ?? [], error: data.error };
  } catch (err) {
    return { calendar: [], error: String(err) };
  }
}
