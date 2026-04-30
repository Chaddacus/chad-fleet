import type { InboxItem, Severity } from '@/lib/types';

async function getInboxItems(): Promise<InboxItem[]> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/inbox`, { cache: 'no-store' });
    if (!res.ok) return [];
    const data = (await res.json()) as { items?: InboxItem[] };
    return data.items ?? [];
  } catch {
    return [];
  }
}

const SEVERITY_CLASSES: Record<Severity, string> = {
  info: 'bg-blue-900 text-blue-300 border border-blue-700',
  warn: 'bg-yellow-900 text-yellow-300 border border-yellow-700',
  critical: 'bg-red-900 text-red-300 border border-red-700',
};

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

export default async function InboxPage() {
  const items = await getInboxItems();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Inbox</h1>
        <p className="mt-1 text-sm text-gray-400">
          Recent notifications from notifier-hub — reverse chronological.
        </p>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-gray-500">No notifications yet.</p>
      ) : (
        <div className="overflow-auto rounded border border-gray-800">
          <table className="w-full text-sm" data-testid="inbox-table">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-xs text-gray-400 uppercase tracking-wider">
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Channel</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Title</th>
                <th className="px-4 py-3">Body</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr
                  key={i}
                  className="border-b border-gray-900 hover:bg-gray-900 transition-colors"
                >
                  <td className="px-4 py-3 whitespace-nowrap text-gray-400 font-mono text-xs">
                    {formatTs(item.ts)}
                  </td>
                  <td className="px-4 py-3 text-gray-300">{item.channel}</td>
                  <td className="px-4 py-3">
                    <SeverityBadge severity={item.severity} />
                  </td>
                  <td className="px-4 py-3 font-medium text-gray-100">{item.title}</td>
                  <td className="px-4 py-3 text-gray-400 max-w-xs truncate" title={item.body}>
                    {item.body}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
