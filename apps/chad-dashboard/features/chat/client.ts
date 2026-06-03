/* Client-side admiral chat stream parser. Posts a conversation to /api/admiral and decodes
 * the OpenAI-shaped SSE deltas, invoking onDelta with each text fragment as it arrives.
 */

import type { ChatMessage, ChatCompletionChunk } from '@/lib/types';

export type { ChatMessage };

/**
 * Stream an admiral reply. Calls `onDelta(text)` for each content fragment; resolves when the
 * stream ends. Throws on transport/HTTP error.
 */
export async function streamChat(
  messages: ChatMessage[],
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/admiral', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || res.body == null) {
    const data = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(data.error ?? `admiral request failed (${res.status})`);
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
      if (!raw || raw === '[DONE]') continue;
      try {
        const chunk = JSON.parse(raw) as ChatCompletionChunk;
        const text = chunk.choices?.[0]?.delta?.content;
        if (text) onDelta(text);
      } catch {
        // ignore malformed SSE lines
      }
    }
  }
}
