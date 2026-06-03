import type { ToolSnapshot } from '@/lib/types';
import { getTools } from './client';

const TRANSPORT_CLASSES: Record<string, string> = {
  stdio: 'bg-purple-900 text-purple-300 border border-purple-700',
  http: 'bg-cyan-900 text-cyan-300 border border-cyan-700',
  sse: 'bg-cyan-900 text-cyan-300 border border-cyan-700',
};

function TransportBadge({ transport }: { transport: string }) {
  const cls = TRANSPORT_CLASSES[transport] ?? 'bg-gray-800 text-gray-300 border border-gray-700';
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${cls}`}>{transport}</span>
  );
}

/** Tools/MCPs tab — the registry of MCP servers the agent can be granted. Read-only. */
export async function ToolsFeature() {
  const { tools, error } = await getTools();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Tools &amp; MCPs</h1>
        <p className="mt-1 text-sm text-gray-400">
          MCP servers registered for the operator. The admiral grants these to captains via
          <code className="mx-1 text-gray-500">allowed_tools</code>; the hub never calls them directly.
        </p>
      </div>

      {error && (
        <div className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
          Couldn’t reach the aggregator: {error}
        </div>
      )}

      {tools.length === 0 && !error ? (
        <p className="text-sm text-gray-500">No MCP servers registered.</p>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="tools-list">
          {tools.map((t: ToolSnapshot) => (
            <li
              key={`${t.source}:${t.name}`}
              className="rounded border border-gray-800 bg-gray-900 px-4 py-3 flex items-center gap-3"
            >
              <TransportBadge transport={t.transport} />
              <span className="text-sm text-gray-100 font-medium">{t.name}</span>
              {t.detail && <span className="text-xs text-gray-500 font-mono truncate">{t.detail}</span>}
              <span className="ml-auto text-xs text-gray-600 uppercase tracking-wide">{t.source}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
