// L2 — App detail. Server-side initial fetch then hands off to L2Client
// for polling + all interactive state.

import Link from 'next/link';
import { notFound } from 'next/navigation';
import type { AppStateBundle } from '@/lib/captainTypes';
import '../captain.css';
import L2Client from './L2Client';

async function fetchApp(appId: string): Promise<AppStateBundle | null> {
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
  try {
    const r = await fetch(
      `${baseUrl}/api/captain/apps/${encodeURIComponent(appId)}`,
      { cache: 'no-store' },
    );
    if (r.status === 404) return null;
    if (!r.ok) return null;
    return (await r.json()) as AppStateBundle;
  } catch {
    return null;
  }
}

export default async function CaptainL2({
  params,
}: {
  params: { appId: string };
}) {
  const initial = await fetchApp(params.appId);
  if (!initial) notFound();

  return (
    <div className="captain-root">
      <div className="cap-bar-row">
        <Link href="/captain" className="back-btn">
          ← fleet
        </Link>
        <span className="l2-app-id">{initial.app_id}</span>
        <span className="l2-mode">{initial.mode}</span>
      </div>
      <L2Client initial={initial} />
    </div>
  );
}
