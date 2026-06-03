/* Server-side admiral proxy. Single responsibility: forward a chat turn to the admiral's
 * OpenAI-compatible endpoint and hand back the streaming SSE response for piping.
 *
 * The dashboard never speaks to captains directly — it speaks to the admiral (:8901), which
 * dispatches. This keeps the agent boundary intact (read via projection, act via agent).
 */

import { config } from '@/lib/config';
import type { ChatMessage } from '@/lib/types';

export type { ChatMessage };

/** POST the conversation to the admiral with streaming on; returns the upstream SSE Response. */
export async function streamAdmiral(
  messages: ChatMessage[],
  signal?: AbortSignal,
): Promise<Response> {
  return fetch(`${config.admiralUrl}/v1/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'admiral', stream: true, messages }),
    signal,
  });
}
