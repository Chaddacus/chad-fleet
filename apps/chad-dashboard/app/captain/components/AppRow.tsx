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
  const isPaused = !!(app.paused_until && new Date(app.paused_until).getTime() > Date.now());
  const isSaturated = isPaused && app.pause_reason === 'backlog_saturated';
  const pauseMinutesLeft = isPaused
    ? Math.max(0, Math.round((new Date(app.paused_until!).getTime() - Date.now()) / 60000))
    : 0;

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
          {(() => {
            const queued = (app.feature_backlog?.items ?? []).filter(i => i.status === 'queued').length;
            return queued > 0 ? <span className="badge b-backlog">{queued} feat</span> : null;
          })()}
        </div>
      </div>

      <div className="app-row-activity">
        <div className="activity-status-line">
          <div className={`activity-pulse${cs ? '' : ' idle'}${isPaused ? (isSaturated ? ' saturated' : ' paused') : ''}`}></div>
          <span className={`activity-status${cs ? ' live' : ''}${isPaused ? (isSaturated ? ' saturated' : ' paused') : ''}`}>
            {isSaturated ? '★ awaiting direction' : isPaused ? '⏸ paused' : cs ? 'in flight' : 'idle'}
          </span>
        </div>
        {isPaused ? (
          <>
            <div className="activity-obj">
              {isSaturated
                ? 'Backlog saturated — awaiting direction'
                : `Circuit breaker tripped — resumes in ~${pauseMinutesLeft}m`}
            </div>
            <div className="activity-meta">
              {isSaturated
                ? 'run `chad-captain ideate --refresh-research` or send admiral note'
                : (app.captain_log_tail[0]
                    ? trunc(app.captain_log_tail[0].rationale, 72)
                    : 'paused by safety guard')}
            </div>
          </>
        ) : cs ? (
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
