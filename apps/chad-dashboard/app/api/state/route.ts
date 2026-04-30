import { NextResponse } from 'next/server';

export async function GET() {
  const upstream =
    process.env.NEXT_PUBLIC_AGGREGATOR_URL ?? 'http://localhost:8106';
  try {
    const r = await fetch(`${upstream}/api/state`, { next: { revalidate: 5 } });
    if (!r.ok) throw new Error(`upstream ${r.status}`);
    return NextResponse.json(await r.json());
  } catch (err: unknown) {
    return NextResponse.json(
      {
        generated_at: new Date().toISOString(),
        error: String(err),
        apps: [],
        inbox_recent: [],
        summary: {},
      },
      { status: 200 },
    );
  }
}
