// L1 — Fleet overview. Server component. Polls via CaptainStatus.

import './captain.css';
import type { FleetBundle } from '@/lib/captainTypes';
import { AppRow } from './components/AppRow';
import CaptainStatus from './CaptainStatus';

async function getFleet(): Promise<{ data: FleetBundle | null; ok: boolean }> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const r = await fetch(`${baseUrl}/api/captain/fleet`, { cache: 'no-store' });
    if (!r.ok) return { data: null, ok: false };
    return { data: (await r.json()) as FleetBundle, ok: true };
  } catch {
    return { data: null, ok: false };
  }
}

export default async function CaptainL1() {
  const { data, ok } = await getFleet();
  const apps = data?.apps ?? [];

  return (
    <div className="captain-root">
      <div className="cap-bar-row">
        <span className="l2-app-id" style={{ fontSize: '13px' }}>
          Captain&apos;s Bridge
        </span>
        <CaptainStatus cadenceMs={15_000} apiOk={ok} />
      </div>

      <div className="l1">
        <div className="l1-header">
          <span className="l1-title">Fleet Overview</span>
          <span className="l1-sub">{apps.length} apps registered</span>
        </div>

        {apps.length === 0 ? (
          <div className="empty">
            <div className="empty-glyph">⚓</div>
            <div className="empty-h">
              {ok ? 'No apps registered' : 'Captain API unreachable'}
            </div>
            <div className="empty-cmd">
              {ok
                ? 'chad-captain register --seed-defaults'
                : 'uv run chad-captain-api --port 8109'}
            </div>
          </div>
        ) : (
          <div className="fleet-list" data-testid="fleet-list">
            {apps.map((app) => (
              <AppRow key={app.app_id} app={app} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
