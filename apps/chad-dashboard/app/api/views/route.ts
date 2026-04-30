import { NextResponse } from 'next/server';

const UPSTREAM = process.env.VIEW_REGISTRY_URL ?? 'http://localhost:8108';

export async function GET() {
  try {
    const r = await fetch(`${UPSTREAM}/views`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`upstream ${r.status}`);
    const items = (await r.json()) as unknown[];
    return NextResponse.json({ items });
  } catch (err: unknown) {
    return NextResponse.json({ items: [], error: String(err) }, { status: 200 });
  }
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'invalid JSON body' }, { status: 400 });
  }
  try {
    const r = await fetch(`${UPSTREAM}/views`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const text = await r.text();
      return NextResponse.json({ error: `upstream ${r.status}: ${text}` }, { status: r.status });
    }
    const view = (await r.json()) as unknown;
    return NextResponse.json({ view }, { status: 201 });
  } catch (err: unknown) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
