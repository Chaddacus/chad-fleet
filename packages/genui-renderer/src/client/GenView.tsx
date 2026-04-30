import React, { useEffect, useReducer, useRef } from 'react';
import type { ViewNode, ViewSpec } from '../schema.js';
import { Card } from './primitives/Card.js';
import { Badge } from './primitives/Badge.js';
import { Table } from './primitives/Table.js';
import { Timeline } from './primitives/Timeline.js';
import { Stat } from './primitives/Stat.js';
import { Chart } from './primitives/Chart.js';

export interface GenViewProps {
  state: object;
  request: string;
  endpoint: string;
  className?: string;
}

type StreamState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'partial'; view: unknown[] }
  | { status: 'final'; view: ViewSpec['view']; narrative?: string }
  | { status: 'error'; message: string };

type Action =
  | { type: 'start' }
  | { type: 'partial'; view: unknown[] }
  | { type: 'final'; view: ViewSpec['view']; narrative?: string }
  | { type: 'error'; message: string }
  | { type: 'reset' };

function reducer(_state: StreamState, action: Action): StreamState {
  switch (action.type) {
    case 'start':
      return { status: 'loading' };
    case 'partial':
      return { status: 'partial', view: action.view };
    case 'final':
      return { status: 'final', view: action.view, narrative: action.narrative };
    case 'error':
      return { status: 'error', message: action.message };
    case 'reset':
      return { status: 'idle' };
  }
}

export function GenView({ state, request, endpoint, className = '' }: GenViewProps): React.ReactElement {
  const [streamState, dispatch] = useReducer(reducer, { status: 'idle' });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // Abort any previous request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    dispatch({ type: 'start' });

    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state, request }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          dispatch({ type: 'error', message: `HTTP ${res.status}` });
          return;
        }
        if (res.body == null) {
          dispatch({ type: 'error', message: 'No response body' });
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
            if (raw === '') continue;

            try {
              const event = JSON.parse(raw) as { type: string; view?: unknown[]; narrative?: string; message?: string };

              if (event.type === 'partial' && Array.isArray(event.view)) {
                dispatch({ type: 'partial', view: event.view });
              } else if (event.type === 'final' && Array.isArray(event.view)) {
                dispatch({
                  type: 'final',
                  view: event.view as ViewSpec['view'],
                  narrative: event.narrative,
                });
              } else if (event.type === 'error') {
                dispatch({ type: 'error', message: event.message ?? 'Unknown error' });
              }
            } catch {
              // Ignore malformed SSE lines
            }
          }
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        dispatch({ type: 'error', message: String(err) });
      });

    return () => {
      controller.abort();
    };
  }, [state, request, endpoint]);

  return (
    <div className={`genui-renderer ${className}`} data-testid="genui-renderer">
      {streamState.status === 'idle' && null}

      {streamState.status === 'loading' && (
        <div className="flex flex-col gap-3 animate-pulse" data-testid="skeleton">
          <div className="h-24 rounded-lg bg-gray-100" />
          <div className="h-16 rounded-lg bg-gray-100" />
        </div>
      )}

      {streamState.status === 'partial' && (
        <div className="flex flex-col gap-3 opacity-60" data-testid="partial-view">
          <div className="text-xs text-gray-400 mb-1">Generating...</div>
        </div>
      )}

      {streamState.status === 'final' && (
        <div className="flex flex-col gap-4" data-testid="final-view">
          {streamState.view.map((node, i) => (
            <React.Fragment key={i}>{renderViewNode(node)}</React.Fragment>
          ))}
          {streamState.narrative != null && (
            <p className="text-sm text-gray-600 mt-2">{streamState.narrative}</p>
          )}
        </div>
      )}

      {streamState.status === 'error' && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
          data-testid="error-view"
        >
          {streamState.message}
        </div>
      )}
    </div>
  );
}

export function renderViewNode(node: ViewNode): React.ReactElement | null {
  switch (node.primitive) {
    case 'Card':
      return (
        <Card
          title={node.title}
          subtitle={node.subtitle}
          tone={node.tone}
          children={node.children}
          renderNode={renderViewNode}
        />
      );
    case 'Badge':
      return <Badge tone={node.tone} label={node.label} />;
    case 'Table':
      return <Table headers={node.headers} rows={node.rows} />;
    case 'Timeline':
      return <Timeline items={node.items} />;
    case 'Stat':
      return <Stat label={node.label} value={node.value} delta={node.delta} tone={node.tone} />;
    case 'Chart':
      return <Chart kind={node.kind} data={node.data} xLabel={node.xLabel} yLabel={node.yLabel} />;
    default:
      return null;
  }
}
