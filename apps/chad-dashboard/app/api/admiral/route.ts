import { NextResponse } from 'next/server';
import { streamAdmiral, type ChatMessage } from '@/features/chat/server';

// Node runtime; gated by middleware (a valid session is required to reach this route).
export const runtime = 'nodejs';

export async function POST(req: Request) {
  let messages: ChatMessage[] = [];
  try {
    const body = (await req.json()) as { messages?: ChatMessage[] };
    messages = Array.isArray(body.messages) ? body.messages : [];
  } catch {
    return NextResponse.json({ error: 'bad request' }, { status: 400 });
  }
  if (messages.length === 0) {
    return NextResponse.json({ error: 'messages required' }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await streamAdmiral(messages, req.signal);
  } catch (err) {
    return NextResponse.json({ error: `admiral unreachable: ${String(err)}` }, { status: 502 });
  }
  if (!upstream.ok || upstream.body == null) {
    return NextResponse.json({ error: `admiral ${upstream.status}` }, { status: 502 });
  }

  // Pass the SSE stream straight through to the browser.
  return new Response(upstream.body, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
    },
  });
}
