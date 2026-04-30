import { headers } from 'next/headers';
import Link from 'next/link';
import ViewClient from './ViewClient';
import type { SavedView, SavedViewResponse } from '@/lib/types';

async function loadView(id: string): Promise<SavedViewResponse> {
  const h = await headers();
  const host = h.get('host') ?? 'localhost:3000';
  const proto = h.get('x-forwarded-proto') ?? 'http';
  try {
    const res = await fetch(`${proto}://${host}/api/views/${encodeURIComponent(id)}`, {
      cache: 'no-store',
    });
    if (res.status === 404) return { view: null, error: 'not found' };
    if (!res.ok) return { view: null, error: `HTTP ${res.status}` };
    const data = (await res.json()) as { view?: SavedView };
    return { view: data.view ?? null };
  } catch (err: unknown) {
    return { view: null, error: String(err) };
  }
}

export default async function ViewDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await loadView(id);

  if (!data.view) {
    return (
      <div data-testid="view-not-found" className="flex flex-col gap-3">
        <h1 className="text-lg font-semibold text-gray-200">View not found</h1>
        <p className="text-sm text-gray-500">No saved view with id <code className="text-gray-300">{id}</code>.</p>
        <Link href="/views" className="text-sm text-gray-400 hover:text-gray-200">← Back to views</Link>
      </div>
    );
  }

  const view = data.view;

  return (
    <div className="flex flex-col gap-4" data-testid="view-detail">
      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-gray-200">{view.name}</h1>
        <Link href="/views" className="text-xs text-gray-500 hover:text-gray-300">← All views</Link>
      </div>
      {view.description && (
        <p className="text-sm text-gray-400">{view.description}</p>
      )}
      <div data-testid="view-prompt" className="rounded border border-gray-800 bg-gray-900 px-4 py-3">
        <span className="block text-xs font-medium uppercase tracking-wider text-gray-600">Prompt</span>
        <p className="mt-1 text-sm text-gray-300 whitespace-pre-wrap">{view.prompt}</p>
      </div>
      <ViewClient viewId={view.id} prompt={view.prompt} />
    </div>
  );
}
