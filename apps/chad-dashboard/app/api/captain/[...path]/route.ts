// Catch-all proxy from the Next dashboard to the captain API on :8109.
// Avoids browser CORS by tunneling every captain request through Next.

import { NextRequest, NextResponse } from 'next/server';

function captainBase(): string {
  return process.env.NEXT_PUBLIC_CAPTAIN_URL ?? 'http://localhost:8109';
}

function buildUpstreamUrl(req: NextRequest, path: string[]): string {
  const search = req.nextUrl.search ?? '';
  return `${captainBase()}/${path.join('/')}${search}`;
}

async function forward(
  req: NextRequest,
  upstream: string,
  init?: RequestInit,
): Promise<NextResponse> {
  try {
    const r = await fetch(upstream, { cache: 'no-store', ...init });
    const text = await r.text();
    const contentType = r.headers.get('content-type') ?? 'application/json';
    return new NextResponse(text, {
      status: r.status,
      headers: { 'content-type': contentType },
    });
  } catch (err: unknown) {
    return NextResponse.json(
      { error: 'captain API unreachable', detail: String(err) },
      { status: 502 },
    );
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: { path: string[] } },
): Promise<NextResponse> {
  return forward(req, buildUpstreamUrl(req, params.path));
}

export async function POST(
  req: NextRequest,
  { params }: { params: { path: string[] } },
): Promise<NextResponse> {
  const body = await req.text();
  return forward(req, buildUpstreamUrl(req, params.path), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body,
  });
}
