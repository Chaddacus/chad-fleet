// L1 fleet row card. Server-component safe — no hooks.

import Link from 'next/link';
import type { AppStateBundle } from '@/lib/captainTypes';
import { Delta, VChip } from './Chips';
import {
  appActivityClass,
  elapsed,
  fmtAgo,
  lastValidate,
  roadmapProgress,
  scoreColor,
  sliceHeadline,
  trunc,
} from '../lib/captainFormat';

export function AppRow({ app }: { app: AppStateBundle }) {
  const cs = app.current_slice;
  const lv = lastValidate(app);
  const progress = roadmapProgress(app.roadmap?.slices);
  const ac = appActivityClass(app);

  const scoreVal = app.scorecard?.aggregate;

  return (
    <Link href={`/captain/${encodeURIComponent(app.app_id)}`} className={`app-row ${ac}`}>
      <div className="app-row-accent"></div>

      <div className="app-row-id">
        <div className="app-row-name">{app.app_id}</div>
        <div className="app-row-badges">
          <span className={`badge ${app.mode === 'autonomous' ? 'b-auto' : 'b-obs'}`}>
            {app.mode === 'autonomous' ? 'autonomous' : 'observe'}
          </span>
          {ac === 'escalating' && <span className="badge b-esc">NEEDS YOU</span>}
          {app.unread_admiral_notes.length > 0 && (
            <span className="unread-pip">{app.unread_admiral_notes.length}</span>
          )}
        </div>
      </div>

      <div className="app-row-activity">
        <div className="activity-status-line">
          <div className={`activity-pulse${cs ? '' : ' idle'}`}></div>
          <span className={`activity-status${cs ? ' live' : ''}`}>
            {cs ? 'in flight' : 'idle'}
          </span>
        </div>
        {cs ? (
          <>
            <div className="activity-obj">{sliceHeadline(cs, 72)}</div>
            <div className="activity-meta">
              {cs.slice_id} · <span>{elapsed(cs.started_at)}</span> elapsed
            </div>
          </>
        ) : (
          <>
            <div className="activity-obj muted">
              {app.captain_log_tail[0]
                ? trunc(app.captain_log_tail[0].rationale, 72)
                : 'No recent activity'}
            </div>
            <div className="activity-meta">
              {app.captain_log_tail[0] ? fmtAgo(app.captain_log_tail[0].ts) : '—'}
            </div>
          </>
        )}
      </div>

      <div className="app-row-metrics">
        <div className="metric-row">
          <span className="metric-lbl">verdict</span>
          {lv ? (
            <VChip verdict={lv.verdict} />
          ) : (
            <span className="metric-val muted">—</span>
          )}
          <Delta v={lv?.rubric_delta_pp} />
        </div>
        <div className="metric-row">
          <span className="metric-lbl">score</span>
          <div className="score-track">
            <div
              className="score-fill"
              style={{
                width: `${(scoreVal ?? 0) * 100}%`,
                background: scoreColor(scoreVal ?? 0),
              }}
            ></div>
          </div>
          <span className="score-num" style={{ color: scoreColor(scoreVal ?? 0) }}>
            {scoreVal !== undefined && scoreVal !== null ? scoreVal.toFixed(2) : '—'}
          </span>
        </div>
        <div className="metric-row">
          <span className="metric-lbl">roadmap</span>
          <span className="metric-val">
            {progress.total > 0 ? `${progress.done}/${progress.total} slices` : '—'}
          </span>
        </div>
      </div>
    </Link>
  );
}
