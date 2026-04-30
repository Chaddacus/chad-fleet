import { NextResponse } from 'next/server';

const UPSTREAM = process.env.VIEW_REGISTRY_URL ?? 'http://localhost:8108';

interface Ctx {
  params: Promise<{ id: string }>;
}

export async function GET(_req: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  try {
    const r = await fetch(`${UPSTREAM}/views/${encodeURIComponent(id)}`, { cache: 'no-store' });
    if (r.status === 404) {
      return NextResponse.json({ view: null, error: 'not found' }, { status: 404 });
    }
    if (!r.ok) throw new Error(`upstream ${r.status}`);
    const view = (await r.json()) as unknown;
    return NextResponse.json({ view });
  } catch (err: unknown) {
    return NextResponse.json({ view: null, error: String(err) }, { status: 502 });
  }
}

export async function DELETE(_req: Request, ctx: Ctx) {
  const { id } = await ctx.params;
  try {
    const r = await fetch(`${UPSTREAM}/views/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (r.status === 404) {
      return NextResponse.json({ error: 'not found' }, { status: 404 });
    }
    if (!r.ok) throw new Error(`upstream ${r.status}`);
    return NextResponse.json({ ok: true });
  } catch (err: unknown) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
