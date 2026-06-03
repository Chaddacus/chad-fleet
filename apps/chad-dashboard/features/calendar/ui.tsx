import type { CalendarEvent } from '@/lib/types';
import { getCalendar } from './client';

/** Calendar tab — read-fast upcoming-events list. Actions (create) route through the admiral,
 * which dispatches a captain holding the calendar-mcp tools (read via projection, act via agent). */
export async function CalendarFeature() {
  const { calendar, error } = await getCalendar();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Calendar</h1>
        <p className="mt-1 text-sm text-gray-400">
          Upcoming events. Ask the admiral to create or move events; it dispatches a captain that
          holds the calendar tools.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
          Couldn’t reach the aggregator: {error}
        </div>
      )}

      {calendar.length === 0 && !error ? (
        <p className="text-sm text-gray-500">
          No events. Configure <code className="text-gray-400">CALENDAR_CALDAV_*</code> to connect a calendar.
        </p>
      ) : (
        <ul className="flex flex-col gap-1" data-testid="calendar-list">
          {calendar.map((e: CalendarEvent) => (
            <li
              key={e.id}
              className="rounded border border-gray-800 bg-gray-950 px-4 py-3 flex items-center gap-3"
            >
              <div className="flex flex-col min-w-0 flex-1">
                <span className="text-sm truncate text-gray-200">{e.summary || '(untitled)'}</span>
                {e.location && <span className="text-xs text-gray-500 truncate">{e.location}</span>}
              </div>
              <span className="text-xs text-gray-500 shrink-0">{e.start}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
