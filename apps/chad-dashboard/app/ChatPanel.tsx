'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { renderViewNode } from '@chad-fleet/genui-renderer/client';
import type { ViewNode } from '@chad-fleet/genui-renderer/client';
import { getGenUiEndpoint } from '@/lib/api';
import { createSavedView } from '@/lib/views';
import type { FleetStateResponse } from '@/lib/types';

// Inline GenView to avoid SSR issues with the package import path
// The component handles streaming itself via fetch + SSE

interface StreamEntry {
  status: 'loading' | 'partial' | 'final' | 'error';
  view?: unknown[];
  narrative?: string;
  message?: string;
}

function StreamView({ entry }: { entry: StreamEntry }) {
  if (entry.status === 'loading') {
    return (
      <div className="flex flex-col gap-3 animate-pulse" data-testid="skeleton">
        <div className="h-16 rounded-lg bg-gray-800" />
        <div className="h-10 rounded-lg bg-gray-800 w-2/3" />
      </div>
    );
  }
  if (entry.status === 'partial') {
    return (
      <div className="text-xs text-gray-500 italic" data-testid="partial-view">
        Generating...
      </div>
    );
  }
  if (entry.status === 'error') {
    return (
      <div
        className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400"
        data-testid="error-view"
      >
        {entry.message}
      </div>
    );
  }
  // final — render the actual primitives (Card/Table/Badge/Stat/Chart/Timeline)
  return (
    <div className="flex flex-col gap-3" data-testid="final-view">
      {entry.narrative && (
        <p className="text-sm text-gray-300">{entry.narrative}</p>
      )}
      {Array.isArray(entry.view) && entry.view.length > 0 && (
        <div className="rounded bg-white px-4 py-4 flex flex-col gap-4 text-gray-900">
          {(entry.view as ViewNode[]).map((node, i) => (
            <React.Fragment key={i}>{renderViewNode(node)}</React.Fragment>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ChatPanel() {
  const [prompt, setPrompt] = useState('');
  const [fleetState, setFleetState] = useState<FleetStateResponse | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [entry, setEntry] = useState<StreamEntry | null>(null);
  const [savedPrompt, setSavedPrompt] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSave = useCallback(async () => {
    if (savedPrompt == null || savedPrompt.trim() === '') return;
    if (entry?.status !== 'final') return;
    const name = window.prompt('Name this view:', savedPrompt.slice(0, 60));
    if (name == null || name.trim() === '') return;
    setSaveStatus('saving');
    setSaveMessage(null);
    const result = await createSavedView({ name: name.trim(), prompt: savedPrompt });
    if (result.error || !result.view) {
      setSaveStatus('error');
      setSaveMessage(result.error ?? 'save failed');
      return;
    }
    setSaveStatus('saved');
    setSaveMessage(`Saved as “${result.view.name}”`);
  }, [savedPrompt, entry]);

  // Poll fleet state every 10s to show status bar
  const loadState = useCallback(async () => {
    try {
      const res = await fetch('/api/state');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as FleetStateResponse;
      setFleetState(data);
      setConnected(!data.error);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    void loadState();
    const id = setInterval(() => { void loadState(); }, 10_000);
    return () => clearInterval(id);
  }, [loadState]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const req = prompt.trim();
      if (!req) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      setEntry({ status: 'loading' });
      setSavedPrompt(req);
      setSaveStatus('idle');
      setSaveMessage(null);

      try {
        const res = await fetch(getGenUiEndpoint(), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ state: fleetState ?? {}, request: req }),
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
              if (event.type === 'partial' && Array.isArray(event.view)) {
                setEntry({ status: 'partial', view: event.view });
              } else if (event.type === 'final' && Array.isArray(event.view)) {
                setEntry({ status: 'final', view: event.view, narrative: event.narrative });
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
    },
    [prompt, fleetState],
  );

  return (
    <div className="flex flex-col gap-4">
      {/* Status bar */}
      <div className="flex items-center gap-2 text-xs text-gray-500" data-testid="status-bar">
        <span
          className={`inline-block h-2 w-2 rounded-full ${
            connected === null
              ? 'bg-gray-600'
              : connected
              ? 'bg-green-500'
              : 'bg-red-500'
          }`}
        />
        {connected === null
          ? 'Checking state-aggregator...'
          : connected
          ? 'Connected to state-aggregator'
          : 'Disconnected from state-aggregator'}
        {fleetState && !fleetState.error && (
          <span className="ml-2 text-gray-600">
            {fleetState.apps?.length ?? 0} apps
          </span>
        )}
      </div>

      {/* Prompt form */}
      <form onSubmit={(e) => { void handleSubmit(e); }} className="flex flex-col gap-3">
        <textarea
          ref={textareaRef}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask the fleet anything..."
          rows={3}
          className="w-full rounded border border-gray-700 bg-gray-900 px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:border-gray-500 focus:outline-none resize-none"
          data-testid="chat-input"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              void handleSubmit(e as unknown as React.FormEvent);
            }
          }}
        />
        <button
          type="submit"
          disabled={!prompt.trim()}
          className="self-end rounded bg-gray-700 px-5 py-2 text-sm font-medium text-gray-100 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Send
        </button>
      </form>

      {/* Streamed view */}
      {entry && (
        <div className="mt-2 rounded border border-gray-800 bg-gray-900 p-4 flex flex-col gap-3">
          <StreamView entry={entry} />
          {entry.status === 'final' && savedPrompt && (
            <div className="flex items-center justify-end gap-2 border-t border-gray-800 pt-3">
              {saveMessage && (
                <span
                  data-testid="save-message"
                  className={`text-xs ${saveStatus === 'error' ? 'text-red-400' : 'text-gray-400'}`}
                >
                  {saveMessage}
                </span>
              )}
              <button
                type="button"
                onClick={() => { void handleSave(); }}
                disabled={saveStatus === 'saving' || saveStatus === 'saved'}
                data-testid="save-view-button"
                className="rounded bg-gray-700 px-3 py-1 text-xs text-gray-100 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saveStatus === 'saving' ? 'Saving…' : saveStatus === 'saved' ? 'Saved' : 'Save view'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
