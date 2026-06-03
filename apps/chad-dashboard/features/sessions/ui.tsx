import type { SessionSnapshot } from '@/lib/types';
import { getSessions } from './client';

const SOURCE_CLASSES: Record<string, string> = {
  claude: 'bg-orange-900 text-orange-300 border border-orange-700',
  'auto-runtime': 'bg-green-900 text-green-300 border border-green-700',
  codex: 'bg-blue-900 text-blue-300 border border-blue-700',
};

function SourceBadge({ source }: { source: string }) {
  const cls = SOURCE_CLASSES[source] ?? 'bg-gray-800 text-gray-300 border border-gray-700';
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{source}</span>
  );
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch {
    return iso;
  }
}

/** Sessions tab — every agent session across runtimes, newest first. */
export async function SessionsFeature() {
  const { sessions, error } = await getSessions();
  const counts = sessions.reduce<Record<string, number>>((acc, s) => {
    acc[s.source] = (acc[s.source] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Sessions</h1>
        <p className="mt-1 text-sm text-gray-400">
          Every agent session across runtimes — Claude, captain tracks, Codex — newest first.
        </p>
        <div className="mt-2 flex gap-3 text-xs text-gray-500">
          {Object.entries(counts).map(([src, n]) => (
            <span key={src}>
              <SourceBadge source={src} /> {n}
            </span>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
          Couldn’t reach the aggregator: {error}
        </div>
      )}

      {sessions.length === 0 && !error ? (
        <p className="text-sm text-gray-500">No sessions found.</p>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="sessions-list">
          {sessions.map((s: SessionSnapshot) => (
            <li
              key={`${s.source}:${s.id}`}
              className="rounded border border-gray-800 bg-gray-900 px-4 py-3 flex items-start gap-3"
            >
              <SourceBadge source={s.source} />
              <div className="flex flex-col min-w-0 flex-1">
                <span className="text-sm text-gray-100 truncate">{s.title || s.id}</span>
                {s.cwd && <span className="text-xs text-gray-500 truncate">{s.cwd}</span>}
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0">
                <span className="text-xs text-gray-500">{formatTs(s.updated_at)}</span>
                {s.status && (
                  <span className="text-xs text-gray-400 uppercase tracking-wide">{s.status}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
