import Link from 'next/link';
import type { AppSnapshot, FleetStateResponse } from '@/lib/types';

async function getApps(): Promise<AppSnapshot[]> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return [];
    const data = (await res.json()) as FleetStateResponse;
    return data.apps ?? [];
  } catch {
    return [];
  }
}

const STATE_CLASSES: Record<string, string> = {
  active: 'bg-green-900 text-green-300 border border-green-700',
  blocked: 'bg-red-900 text-red-300 border border-red-700',
  idle: 'bg-gray-800 text-gray-400 border border-gray-600',
  paused: 'bg-yellow-900 text-yellow-300 border border-yellow-700',
};

function StateBadge({ state }: { state: string }) {
  const cls =
    STATE_CLASSES[state.toLowerCase()] ??
    'bg-gray-800 text-gray-400 border border-gray-600';
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {state}
    </span>
  );
}

function ModeBadge({ mode }: { mode: string }) {
  return (
    <span className="inline-block rounded px-2 py-0.5 text-xs font-medium bg-blue-900 text-blue-300 border border-blue-700">
      {mode}
    </span>
  );
}

function relativeTime(isoString: string): string {
  try {
    const diffMs = Date.now() - new Date(isoString).getTime();
    const diffSec = Math.floor(diffMs / 1000);
    if (diffSec < 60) return `${diffSec}s ago`;
    const diffMin = Math.floor(diffSec / 60);
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return `${Math.floor(diffHr / 24)}d ago`;
  } catch {
    return isoString;
  }
}

export default async function AppsPage() {
  const apps = await getApps();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Apps</h1>
        <p className="mt-1 text-sm text-gray-400">
          All registered apps tracked by state-aggregator.
        </p>
      </div>

      {apps.length === 0 ? (
        <p className="text-sm text-gray-500" data-testid="empty-state">
          No apps tracked yet. Enroll one with{' '}
          <code className="rounded bg-gray-800 px-1 py-0.5 text-xs text-gray-300">
            tracked-app-registry add ...
          </code>
        </p>
      ) : (
        <div className="grid gap-4" data-testid="apps-list">
          {apps.map((app) => (
            <Link
              key={app.id}
              href={`/apps/${app.id}`}
              className="block rounded border border-gray-800 bg-gray-900 p-5 hover:border-gray-700 hover:bg-gray-800 transition-colors"
              data-testid={`app-card-${app.id}`}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <h2 className="font-semibold text-gray-100 truncate">{app.name}</h2>
                  <p className="text-xs text-gray-500 font-mono mt-0.5">{app.id}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <StateBadge state={app.state} />
                  <ModeBadge mode={app.mode} />
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400">
                <span>last progress: {relativeTime(app.last_progress_at)}</span>
                <span>owner: {app.owner_brand}</span>
                {app.blocked_reason && (
                  <span className="text-red-400">blocked: {app.blocked_reason}</span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
