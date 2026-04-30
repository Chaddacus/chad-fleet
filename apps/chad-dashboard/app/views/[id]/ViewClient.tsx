'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { renderViewNode } from '@chad-fleet/genui-renderer/client';
import type { ViewNode } from '@chad-fleet/genui-renderer/client';
import { getGenUiEndpoint } from '@/lib/api';
import type { FleetStateResponse } from '@/lib/types';

interface StreamEntry {
  status: 'idle' | 'loading' | 'partial' | 'final' | 'error';
  view?: unknown[];
  narrative?: string;
  message?: string;
}

interface Props {
  viewId: string;
  prompt: string;
}

export default function ViewClient({ viewId, prompt }: Props) {
  const [entry, setEntry] = useState<StreamEntry>({ status: 'idle' });
  const abortRef = useRef<AbortController | null>(null);

  const replay = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setEntry({ status: 'loading' });

    let fleetState: FleetStateResponse = {
      generated_at: '',
      apps: [],
      inbox_recent: [],
      summary: {},
    };
    try {
      const stateRes = await fetch('/api/state');
      if (stateRes.ok) fleetState = (await stateRes.json()) as FleetStateResponse;
    } catch {
      // Continue with empty state — render service handles it.
    }

    try {
      const res = await fetch(getGenUiEndpoint(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state: fleetState, request: prompt }),
        signal: controller.signal,
      });

      if (!res.ok || res.body == null) {
        setEntry({ status: 'error', message: `HTTP ${res.status}` });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const event = JSON.parse(raw) as {
              type: string;
              view?: unknown[];
              narrative?: string;
              message?: string;
            };
            if (event.type === 'partial') {
              setEntry({ status: 'partial' });
            } else if (event.type === 'final' && Array.isArray(event.view)) {
              setEntry({
                status: 'final',
                view: event.view,
                narrative: event.narrative,
              });
            } else if (event.type === 'error') {
              setEntry({ status: 'error', message: event.message ?? 'Unknown error' });
            }
          } catch {
            // ignore malformed SSE
          }
        }
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      setEntry({ status: 'error', message: String(err) });
    }
  }, [prompt]);

  useEffect(() => {
    void replay();
    return () => abortRef.current?.abort();
  }, [replay]);

  return (
    <div data-testid={`view-render-${viewId}`} className="rounded border border-gray-800 bg-gray-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wider text-gray-600">
          Rendered view
        </span>
        <button
          type="button"
          onClick={() => { void replay(); }}
          className="rounded bg-gray-800 px-3 py-1 text-xs text-gray-300 hover:bg-gray-700"
        >
          Re-render
        </button>
      </div>
      {entry.status === 'loading' && (
        <div data-testid="view-loading" className="flex flex-col gap-3 animate-pulse">
          <div className="h-16 rounded-lg bg-gray-800" />
          <div className="h-10 rounded-lg bg-gray-800 w-2/3" />
        </div>
      )}
      {entry.status === 'partial' && (
        <div data-testid="view-partial" className="text-xs text-gray-500 italic">
          Generating...
        </div>
      )}
      {entry.status === 'error' && (
        <div data-testid="view-error" className="rounded border border-red-700 bg-red-950 px-3 py-2 text-sm text-red-400">
          {entry.message}
        </div>
      )}
      {entry.status === 'final' && (
        <div data-testid="view-final" className="flex flex-col gap-3">
          {entry.narrative && <p className="text-sm text-gray-300">{entry.narrative}</p>}
          {Array.isArray(entry.view) && entry.view.length > 0 && (
            <div className="rounded bg-white px-4 py-4 flex flex-col gap-4 text-gray-900">
              {(entry.view as ViewNode[]).map((node, i) => (
                <React.Fragment key={i}>{renderViewNode(node)}</React.Fragment>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
