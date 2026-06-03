'use client';

import { useRef, useState } from 'react';
import { streamChat, type ChatMessage } from './client';

interface Turn extends ChatMessage {
  streaming?: boolean;
}

/** Main hub surface — chat tied to the admiral. Each turn streams the admiral's dispatch. */
export function ChatFeature() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    setError(null);
    setInput('');
    setBusy(true);

    const history: ChatMessage[] = [
      ...turns.map(({ role, content }) => ({ role, content })),
      { role: 'user', content: text },
    ];
    setTurns((t) => [...t, { role: 'user', content: text }, { role: 'assistant', content: '', streaming: true }]);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        history,
        (delta) => {
          setTurns((t) => {
            const next = [...t];
            const last = next[next.length - 1];
            if (last && last.role === 'assistant') {
              next[next.length - 1] = { ...last, content: last.content + delta };
            }
            return next;
          });
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) setError(String(err));
    } finally {
      setBusy(false);
      setTurns((t) => {
        const next = [...t];
        const last = next[next.length - 1];
        if (last && last.role === 'assistant') next[next.length - 1] = { ...last, streaming: false };
        return next;
      });
    }
  }

  return (
    <div className="flex flex-col gap-4" data-testid="admiral-chat">
      <div className="flex flex-col gap-3 min-h-[40vh]">
        {turns.length === 0 && (
          <p className="text-sm text-gray-600">
            Command the admiral. Describe a task and it will dispatch the fleet.
          </p>
        )}
        {turns.map((turn, i) => (
          <div
            key={i}
            className={`rounded border px-4 py-3 text-sm whitespace-pre-wrap ${
              turn.role === 'user'
                ? 'border-gray-700 bg-gray-900 text-gray-100'
                : 'border-gray-800 bg-gray-950 text-gray-300'
            }`}
            data-testid={`turn-${turn.role}`}
          >
            <span className="mr-2 text-xs uppercase tracking-wide text-gray-500">
              {turn.role === 'user' ? 'you' : 'admiral'}
            </span>
            {turn.content}
            {turn.streaming && <span className="ml-1 animate-pulse text-gray-500">▋</span>}
          </div>
        ))}
      </div>

      {error && (
        <div className="rounded border border-red-700 bg-red-950 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      <form onSubmit={send} className="flex flex-col gap-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Describe a task for the fleet…"
          rows={3}
          data-testid="chat-input"
          className="w-full rounded border border-gray-700 bg-gray-900 px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:border-gray-500 focus:outline-none resize-none"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void send(e as unknown as React.FormEvent);
          }}
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="self-end rounded bg-gray-700 px-5 py-2 text-sm font-medium text-gray-100 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {busy ? 'Dispatching…' : 'Send'}
        </button>
      </form>
    </div>
  );
}
