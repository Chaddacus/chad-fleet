import Link from 'next/link';
import { headers } from 'next/headers';
import type { SavedView, SavedViewListResponse } from '@/lib/types';

async function loadViews(): Promise<SavedViewListResponse> {
  const h = await headers();
  const host = h.get('host') ?? 'localhost:3000';
  const proto = h.get('x-forwarded-proto') ?? 'http';
  try {
    const res = await fetch(`${proto}://${host}/api/views`, { cache: 'no-store' });
    if (!res.ok) return { items: [], error: `HTTP ${res.status}` };
    return (await res.json()) as SavedViewListResponse;
  } catch (err: unknown) {
    return { items: [], error: String(err) };
  }
}

function pinnedFirst(items: SavedView[]): SavedView[] {
  return [...items].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.created_at.localeCompare(a.created_at);
  });
}

export default async function ViewsPage() {
  const data = await loadViews();
  const items = pinnedFirst(data.items);

  if (data.error) {
    return (
      <div data-testid="views-error" className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
        Failed to load views: {data.error}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div data-testid="views-empty" className="rounded border border-gray-800 bg-gray-900 px-4 py-8 text-center text-sm text-gray-500">
        No saved views yet. Use the Save button on the chat panel to capture one.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3" data-testid="views-list">
      <h1 className="text-lg font-semibold text-gray-200">Saved views</h1>
      <ul className="flex flex-col gap-2">
        {items.map((v) => (
          <li
            key={v.id}
            data-testid={`view-row-${v.id}`}
            className="flex items-center justify-between rounded border border-gray-800 bg-gray-900 px-4 py-3 hover:border-gray-700"
          >
            <Link href={`/views/${v.id}`} className="flex flex-col gap-1 flex-1">
              <span className="text-sm font-medium text-gray-100 flex items-center gap-2">
                {v.pinned && <span data-testid={`pin-${v.id}`} className="text-yellow-500">●</span>}
                {v.name}
              </span>
              {v.description && (
                <span className="text-xs text-gray-500">{v.description}</span>
              )}
              <span className="text-xs text-gray-600 truncate max-w-2xl">
                {v.prompt}
              </span>
            </Link>
            {v.tags.length > 0 && (
              <div className="flex gap-1">
                {v.tags.map((t) => (
                  <span key={t} className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
