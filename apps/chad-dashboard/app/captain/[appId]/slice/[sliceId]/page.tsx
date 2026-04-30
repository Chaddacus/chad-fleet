// L3 — Slice evidence. Server fetch, mostly static; no polling needed
// because slice evidence is point-in-time.

import Link from 'next/link';
import { notFound } from 'next/navigation';
import type {
  AppStateBundle,
  CaptainLogEntry,
  ProgressEvent,
} from '@/lib/captainTypes';
import '../../../captain.css';
import { Delta, VChip } from '../../../components/Chips';
import {
  fmtAgo,
  fmtDetail,
  fmtTime,
  trunc,
} from '../../../lib/captainFormat';

async function fetchApp(appId: string): Promise<AppStateBundle | null> {
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
  try {
    const r = await fetch(
      `${baseUrl}/api/captain/apps/${encodeURIComponent(appId)}`,
      { cache: 'no-store' },
    );
    if (!r.ok) return null;
    return (await r.json()) as AppStateBundle;
  } catch {
    return null;
  }
}

async function fetchDiff(diffPath: string): Promise<string | null> {
  // Captain's slice_complete.diff_path points at a host-local file. The
  // dashboard server has the same fs view; read it directly.
  if (!diffPath) return null;
  try {
    const fs = await import('node:fs/promises');
    return await fs.readFile(diffPath, 'utf-8');
  } catch {
    return null;
  }
}

export default async function CaptainL3({
  params,
}: {
  params: { appId: string; sliceId: string };
}) {
  const app = await fetchApp(params.appId);
  if (!app) notFound();

  const stripRetry = (sid: string) => sid.replace(/-retry$/, '');
  const target = stripRetry(params.sliceId);

  const sliceInfo = app.roadmap?.slices.find(
    (s) => s.slice_id === target || s.slice_id === params.sliceId,
  );
  const cs =
    app.current_slice?.slice_id === params.sliceId ||
    app.current_slice?.slice_id === target
      ? app.current_slice
      : null;

  const sliceLogs: CaptainLogEntry[] = app.captain_log_tail.filter(
    (e) => e.slice_id === params.sliceId || e.slice_id === target,
  );
  const sliceProgress: ProgressEvent[] = cs
    ? app.progress_tail
    : app.progress_tail.filter(
        (e) => e.slice_id === params.sliceId || e.slice_id === target,
      );

  const diffPath = sliceLogs
    .map((e) => (e.references as { diff_path?: string })?.diff_path)
    .find((p): p is string => Boolean(p));
  const diffText = diffPath ? await fetchDiff(diffPath) : null;

  return (
    <div className="captain-root">
      <div className="cap-bar-row">
        <Link href={`/captain/${encodeURIComponent(params.appId)}`} className="back-btn">
          ← {params.appId}
        </Link>
        <span className="l3-title">{params.sliceId}</span>
        {sliceInfo?.phase && (
          <span className="l2-mode" style={{ marginLeft: '8px' }}>
            phase: {sliceInfo.phase}
          </span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: '9px', color: 'var(--t3)' }}>
          slice evidence
        </span>
      </div>

      <div className="l3-meta-bar">
        <div className="l3-obj">
          {sliceInfo?.objective || cs?.objective || params.sliceId}
        </div>
        <div className="l3-kvs">
          {cs && (
            <>
              <span className="l3-kv">
                issued <span>{fmtTime(cs.issued_at)}</span>
              </span>
              <span className="l3-kv">
                started <span>{fmtTime(cs.started_at)}</span>
              </span>
              {cs.parent_slice_id && (
                <span className="l3-kv" style={{ color: 'var(--orange)' }}>
                  ↻ retry of {cs.parent_slice_id}
                </span>
              )}
            </>
          )}
          {sliceInfo && (
            <>
              <span className="l3-kv">
                phase <span>{sliceInfo.phase}</span>
              </span>
              <span className="l3-kv">
                est <span>{sliceInfo.estimated_minutes}min</span>
              </span>
            </>
          )}
        </div>
      </div>

      <div className="l3-panels">
        <div className="l3-panel">
          <div className="l3-panel-hd">
            <span className="pane-title">Captain Log · {params.sliceId}</span>
          </div>
          <div className="l3-panel-body">
            {sliceLogs.length === 0 && sliceProgress.length === 0 && (
              <div style={{ fontSize: '11px', color: 'var(--t3)' }}>
                No log entries scoped to this slice.
              </div>
            )}
            {sliceLogs.map((e, i) => (
              <LogEvidence key={`${e.ts}-${i}`} entry={e} />
            ))}
            {sliceProgress.length > 0 && (
              <>
                <div className="sec-lbl">Progress Events</div>
                <div className="prog-stream">
                  {sliceProgress.map((ev, i) => (
                    <PEvtServer key={`${ev.ts}-${i}`} ev={ev} />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        <div className="l3-panel">
          <div className="l3-panel-hd">
            <span className="pane-title">Diff Evidence</span>
          </div>
          <div className="l3-panel-body">
            {diffText && diffPath ? (
              <>
                <div style={{ fontSize: '9px', color: 'var(--t3)', marginBottom: '4px' }}>
                  {diffPath}
                </div>
                <DiffView diff={diffText} />
              </>
            ) : diffPath ? (
              <div style={{ fontSize: '10px', color: 'var(--t3)' }}>
                Diff path on file but unreadable: {diffPath}
              </div>
            ) : cs ? (
              <>
                <div style={{ fontSize: '10px', color: 'var(--t3)', marginBottom: '10px' }}>
                  Slice in flight — diff available after completion.
                </div>
                {cs.expected_rubric_categories.length > 0 && (
                  <div className="slice-box">
                    <div className="slice-glyph">Expected rubric categories</div>
                    {cs.expected_rubric_categories.map((c) => (
                      <div
                        key={c}
                        style={{
                          fontSize: '10px',
                          color: 'var(--t2)',
                          padding: '4px 0',
                          borderBottom: '1px solid var(--t4)',
                        }}
                      >
                        {c}
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div style={{ fontSize: '10px', color: 'var(--t3)' }}>
                No diff_path in log references for this slice.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function LogEvidence({ entry }: { entry: CaptainLogEntry }) {
  const hasRefs = Object.keys(entry.references || {}).length > 0;
  return (
    <div className="log-row">
      <div className="log-row-head">
        <span className="log-kind">{entry.kind}</span>
        <VChip verdict={entry.verdict} />
        <Delta v={entry.rubric_delta_pp} />
        <span className="log-ts">{fmtAgo(entry.ts)}</span>
      </div>
      <div className="log-rat">{trunc(entry.rationale, 500)}</div>
      {hasRefs && (
        <pre className="log-refs">{JSON.stringify(entry.references, null, 2)}</pre>
      )}
    </div>
  );
}

function PEvtServer({ ev }: { ev: ProgressEvent }) {
  const detail = fmtDetail(ev.detail);
  return (
    <div className="prog-evt">
      <span className="pe-ts">{fmtTime(ev.ts).slice(0, 5)}</span>
      <span className={`pe-kind ${ev.kind}`}>{ev.kind}</span>
      <span className="pe-detail">{detail}</span>
    </div>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff-pre">
      {diff.split('\n').map((line, i) => {
        let cls = 'd-ctx';
        if (line.startsWith('+++') || line.startsWith('---')) cls = 'd-hdr';
        else if (line.startsWith('+')) cls = 'd-add';
        else if (line.startsWith('-')) cls = 'd-rem';
        else if (line.startsWith('@@')) cls = 'd-hdr';
        return (
          <div key={i} className={cls}>
            {line}
          </div>
        );
      })}
    </pre>
  );
}
