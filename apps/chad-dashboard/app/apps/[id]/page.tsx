import type { AppSnapshot, FleetStateResponse, InboxItem, Severity, ObsessiveLoopRun } from '@/lib/types';

async function getFleetState(): Promise<FleetStateResponse> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return { apps: [], inbox_recent: [], summary: {}, generated_at: '' };
    return (await res.json()) as FleetStateResponse;
  } catch {
    return { apps: [], inbox_recent: [], summary: {}, generated_at: '' };
  }
}

const SEVERITY_CLASSES: Record<Severity, string> = {
  info: 'bg-blue-900 text-blue-300 border border-blue-700',
  warn: 'bg-yellow-900 text-yellow-300 border border-yellow-700',
  critical: 'bg-red-900 text-red-300 border border-red-700',
};

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

function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${SEVERITY_CLASSES[severity]}`}>
      {severity}
    </span>
  );
}

function formatTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return iso;
  }
}

interface MetaRowProps {
  label: string;
  value: string | null | undefined;
}

function MetaRow({ label, value }: MetaRowProps) {
  if (value == null) return null;
  return (
    <tr className="border-b border-gray-900">
      <td className="py-2 pr-6 text-xs text-gray-500 w-40 align-top">{label}</td>
      <td className="py-2 font-mono text-xs text-gray-200 break-all">{value}</td>
    </tr>
  );
}

interface PageProps {
  params: Promise<{ id: string }>;
}

export default async function AppDetailPage({ params }: PageProps) {
  const { id } = await params;
  const state = await getFleetState();
  const app: AppSnapshot | undefined = state.apps.find((a) => a.id === id);

  if (!app) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="font-mono text-2xl font-bold text-gray-100">App not found</h1>
        <p className="text-sm text-gray-400">
          No app with id <code className="rounded bg-gray-800 px-1 py-0.5 text-xs">{id}</code>{' '}
          was found in the registry.
        </p>
      </div>
    );
  }

  // Filter inbox items by channel containing the app id; fall back to all
  const allInbox: InboxItem[] = state.inbox_recent ?? [];
  const filteredInbox = allInbox.filter((item) => item.channel.includes(id));
  const inboxItems = filteredInbox.length > 0 ? filteredInbox : allInbox;
  const recentInbox = inboxItems.slice(0, 20);

  return (
    <div className="flex flex-col gap-8">
      {/* Header */}
      <div className="flex items-center gap-4">
        <h1 className="font-mono text-2xl font-bold text-gray-100">{app.name}</h1>
        <StateBadge state={app.state} />
      </div>

      {/* Metadata */}
      <section data-testid="section-metadata">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
          Metadata
        </h2>
        <div className="rounded border border-gray-800 bg-gray-900 p-4">
          <table className="w-full">
            <tbody>
              <MetaRow label="id" value={app.id} />
              <MetaRow label="mode" value={app.mode} />
              <MetaRow label="cadence" value={app.cadence} />
              <MetaRow label="owner_brand" value={app.owner_brand} />
              <MetaRow label="last_progress_at" value={app.last_progress_at} />
              <MetaRow label="blocked_reason" value={app.blocked_reason ?? null} />
              <MetaRow
                label="repo_path"
                value={
                  typeof app.metadata?.repo_path === 'string'
                    ? app.metadata.repo_path
                    : null
                }
              />
            </tbody>
          </table>
        </div>
      </section>

      {/* Obsessive-loop runs */}
      <section data-testid="section-runs">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
          Obsessive-loop runs
        </h2>
        {app.obsessive_loop_runs.length === 0 ? (
          <p className="text-sm text-gray-500">No runs recorded yet.</p>
        ) : (
          <div className="overflow-auto rounded border border-gray-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 bg-gray-900 text-left text-xs text-gray-400 uppercase tracking-wider">
                  <th className="px-4 py-3">Run ID</th>
                  <th className="px-4 py-3">Branch</th>
                  <th className="px-4 py-3">Baseline weighted_avg</th>
                  <th className="px-4 py-3">Repo</th>
                </tr>
              </thead>
              <tbody>
                {app.obsessive_loop_runs.map((run: ObsessiveLoopRun) => (
                  <tr
                    key={run.run_id}
                    className="border-b border-gray-900 hover:bg-gray-900 transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-xs text-gray-300">{run.run_id}</td>
                    <td className="px-4 py-3 text-gray-400">{run.branch ?? '—'}</td>
                    <td className="px-4 py-3 text-gray-400">
                      {run.weighted_avg != null ? run.weighted_avg.toFixed(3) : '—'}
                    </td>
                    <td className="px-4 py-3 text-gray-400 font-mono text-xs">
                      {typeof run.repo === 'string' ? run.repo : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Recent inbox */}
      <section data-testid="section-inbox">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-gray-500">
          Recent inbox
        </h2>
        {recentInbox.length === 0 ? (
          <p className="text-sm text-gray-500">No inbox items.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {recentInbox.map((item, i) => (
              <div
                key={i}
                className="flex items-start gap-3 rounded border border-gray-800 bg-gray-900 px-4 py-3"
              >
                <span className="whitespace-nowrap text-xs font-mono text-gray-500 mt-0.5 w-32 flex-shrink-0">
                  {formatTs(item.ts)}
                </span>
                <SeverityBadge severity={item.severity} />
                <span className="text-sm text-gray-200 font-medium">{item.title}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
