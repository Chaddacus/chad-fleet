'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AppStateBundle,
  CaptainLogEntry,
  ProgressEvent,
  RoadmapSlice,
} from '@/lib/captainTypes';
import { Delta, VChip } from '../components/Chips';
import {
  elapsed,
  fmtAgo,
  fmtDetail,
  fmtTime,
  scoreColor,
  sliceHeadline,
  trunc,
} from '../lib/captainFormat';
import { BASELINE_DIMENSIONS } from '@/lib/captainTypes';

interface Props {
  initial: AppStateBundle;
}

export default function L2Client({ initial }: Props) {
  const [app, setApp] = useState<AppStateBundle>(initial);
  const [stamp, setStamp] = useState('just now');
  const [apiOk, setApiOk] = useState(true);
  const lastRefresh = useRef(Date.now());
  const progRef = useRef<HTMLDivElement | null>(null);

  const refetch = useCallback(async () => {
    try {
      const r = await fetch(
        `/api/captain/apps/${encodeURIComponent(initial.app_id)}`,
        { cache: 'no-store' },
      );
      if (!r.ok) {
        setApiOk(false);
        return;
      }
      const next = (await r.json()) as AppStateBundle;
      setApp(next);
      setApiOk(true);
      lastRefresh.current = Date.now();
      setStamp('just now');
    } catch {
      setApiOk(false);
    }
  }, [initial.app_id]);

  const cadenceMs = app.current_slice ? 5_000 : 15_000;

  useEffect(() => {
    const id = setInterval(refetch, cadenceMs);
    return () => clearInterval(id);
  }, [refetch, cadenceMs]);

  useEffect(() => {
    const t = setInterval(() => {
      const s = Math.round((Date.now() - lastRefresh.current) / 1000);
      setStamp(s < 5 ? 'just now' : `${s}s ago`);
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // Auto-scroll progress stream to bottom on update.
  useEffect(() => {
    if (progRef.current) progRef.current.scrollTop = progRef.current.scrollHeight;
  }, [app]);

  const cs = app.current_slice;
  const rm = app.roadmap;
  const prStatus = derivePrStatus(app.captain_log_tail);

  return (
    <div className="l2">
      <div className="cap-bar-row" style={{ borderTop: 0 }}>
        {app.unread_admiral_notes.length > 0 && (
          <span className="unread-pip">{app.unread_admiral_notes.length}</span>
        )}
        <span style={{ marginLeft: 'auto' }}>
          <span className={`cap-status-dot${apiOk ? '' : ' err'}`}>
            {apiOk ? (cs ? 'live · 5s' : 'idle · 15s') : 'unreachable'}
          </span>
        </span>
        <span className="cap-tick-stamp">{stamp}</span>
      </div>

      {prStatus && <PrStatusBanner status={prStatus} />}

      <div className="l2-panes">
        {/* Pane A — current slice + progress */}
        <div className="pane">
          <div className="pane-hd">
            <span className="pane-title">Current Slice</span>
            {cs ? (
              <span className="pane-tag" style={{ color: 'var(--green)' }}>in flight</span>
            ) : (
              <span className="pane-tag">idle</span>
            )}
          </div>
          <div className="pane-body" ref={progRef}>
            {cs ? (
              <div className="slice-box live">
                {cs.parent_slice_id && (
                  <div className="retry-tag">↻ retry · parent: {cs.parent_slice_id}</div>
                )}
                <div className="slice-glyph">
                  <span>{cs.slice_id}</span>
                  <span style={{ color: 'var(--t3)' }}>·</span>
                  <span style={{ color: 'var(--green)' }}>● in flight</span>
                </div>
                <div className="slice-obj">{sliceHeadline(cs, 200)}</div>
                {cs.title && cs.title !== cs.objective && (
                  <details className="slice-obj-full">
                    <summary>full objective</summary>
                    <div className="slice-obj-detail">{cs.objective}</div>
                  </details>
                )}
                <div className="slice-kv">
                  <KV k="started" v={fmtTime(cs.started_at)} />
                  <KV k="elapsed" v={elapsed(cs.started_at)} live />
                  <KV k="max_turns" v={String(cs.max_turns)} />
                  <KV k="deadline" v={fmtTime(cs.deadline)} />
                </div>
              </div>
            ) : (
              <div className="slice-box">
                <div className="idle-mark">
                  idle since {fmtAgo(app.captain_log_tail[0]?.ts)}
                </div>
                {app.captain_log_tail[0] && (
                  <div className="log-rat" style={{ marginTop: '8px' }}>
                    {trunc(app.captain_log_tail[0].rationale, 120)}
                  </div>
                )}
              </div>
            )}

            <div className="prog-section-lbl">Progress Stream</div>
            {app.progress_tail.length > 0 ? (
              <div className="prog-stream">
                {app.progress_tail.map((ev, i) => (
                  <PEvt key={`${ev.ts}-${i}`} ev={ev} />
                ))}
              </div>
            ) : (
              <div style={{ fontSize: '10px', color: 'var(--t3)' }}>No progress events.</div>
            )}
          </div>
        </div>

        {/* Pane B — roadmap + log */}
        <div className="pane">
          <div className="pane-hd">
            <span className="pane-title">Roadmap</span>
            {rm && (
              <span className="pane-tag">
                {rm.slices.filter((s) => s.status === 'done').length}/{rm.slices.length}
              </span>
            )}
          </div>
          <div className="pane-body">
            <RoadmapPane appId={app.app_id} roadmap={rm} />
            <div className="sec-lbl" style={{ marginTop: '8px' }}>Captain Log</div>
            {app.captain_log_tail.length === 0 && (
              <div style={{ fontSize: '11px', color: 'var(--t3)' }}>No log entries.</div>
            )}
            {app.captain_log_tail.map((e, i) => (
              <LogRow key={`${e.ts}-${i}`} entry={e} />
            ))}
          </div>
        </div>

        {/* Pane C — admiral console */}
        <div className="pane">
          <div className="pane-hd">
            <span className="pane-title">Admiral Console</span>
          </div>
          <div className="pane-body">
            <AdmiralConsole app={app} onAfter={refetch} />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── small subcomponents ─────────────────────────────────────────── */

function KV({ k, v, live }: { k: string; v: string; live?: boolean }) {
  return (
    <div className="kv">
      <span className="kv-k">{k}</span>
      <span className={`kv-v${live ? ' live' : ''}`}>{v}</span>
    </div>
  );
}

function PEvt({ ev }: { ev: ProgressEvent }) {
  const [exp, setExp] = useState(false);
  const detail = fmtDetail(ev.detail);
  const long = detail.length > 55;
  return (
    <div className="prog-evt">
      <span className="pe-ts">{fmtTime(ev.ts).slice(0, 5)}</span>
      <span className={`pe-kind ${ev.kind}`}>{ev.kind}</span>
      <span className="pe-detail">
        {long && !exp ? trunc(detail, 55) : detail}
        {long && (
          <span className="expand-toggle" onClick={() => setExp((e) => !e)}>
            {exp ? ' ↑' : ' …'}
          </span>
        )}
      </span>
    </div>
  );
}

function LogRow({ entry }: { entry: CaptainLogEntry }) {
  const [exp, setExp] = useState(false);
  const hasRefs = Object.keys(entry.references || {}).length > 0;
  return (
    <div className="log-row" onClick={() => setExp((e) => !e)}>
      <div className="log-row-head">
        <span className="log-kind">{entry.kind}</span>
        <VChip verdict={entry.verdict} />
        <Delta v={entry.rubric_delta_pp} />
        <span className="log-ts">{fmtAgo(entry.ts)}</span>
      </div>
      <div className="log-rat">{trunc(entry.rationale, exp ? 500 : 140)}</div>
      {exp && hasRefs && (
        <pre className="log-refs">{JSON.stringify(entry.references, null, 2)}</pre>
      )}
    </div>
  );
}

function RoadmapPane({
  appId,
  roadmap,
}: {
  appId: string;
  roadmap: AppStateBundle['roadmap'];
}) {
  if (!roadmap) {
    return (
      <div className="log-rat" style={{ color: 'var(--t3)' }}>
        No roadmap on file.
      </div>
    );
  }
  const done = roadmap.slices.filter((s) => s.status === 'done').length;
  const total = roadmap.slices.length;
  const pct = total ? (done / total) * 100 : 0;
  return (
    <>
      <div className="rm-summary">{trunc(roadmap.objective_summary, 110)}</div>
      <div className="rm-stats">
        <span className="rm-stat">
          {done}/{total} done
        </span>
        <span className="rm-stat">
          by <span>{roadmap.generated_by}</span>
        </span>
        <span className="rm-stat">
          <span>{fmtAgo(roadmap.generated_at)}</span>
        </span>
      </div>
      <div className="rm-prog-track">
        <div className="rm-prog-fill" style={{ width: `${pct}%` }}></div>
      </div>
      {roadmap.slices.map((sl) => (
        <RoadmapSliceRow key={sl.slice_id} appId={appId} sl={sl} />
      ))}
    </>
  );
}

function RoadmapSliceRow({ appId, sl }: { appId: string; sl: RoadmapSlice }) {
  const colors: Record<string, { c: string; bg: string; bd: string }> = {
    done: { c: 'var(--green)', bg: 'var(--green-d)', bd: 'var(--green-bd)' },
    in_flight: { c: 'var(--yellow)', bg: 'var(--yellow-d)', bd: 'var(--yellow-bd)' },
    queued: { c: 'var(--t3)', bg: 'transparent', bd: 'var(--border)' },
    blocked: { c: 'var(--orange)', bg: 'var(--orange-d)', bd: 'var(--orange-bd)' },
    skipped: { c: 'var(--t3)', bg: 'transparent', bd: 'var(--border)' },
  };
  const cs = colors[sl.status] ?? colors.queued;
  return (
    <Link
      href={`/captain/${encodeURIComponent(appId)}/slice/${encodeURIComponent(sl.slice_id)}`}
      className="rm-slice"
    >
      <div className="rm-dot-wrap">
        <div className={`rm-dot ${sl.status}`}></div>
      </div>
      <div className="rm-body">
        <div className="rm-id">{sl.slice_id}</div>
        <div className={`rm-obj ${sl.status}`} title={sl.objective}>{sliceHeadline(sl, 110)}</div>
        {sl.phase && (
          <div className="rm-phase">
            {sl.phase} · ~{sl.estimated_minutes}min
          </div>
        )}
        {sl.blocked_by.length > 0 && (
          <div className="rm-blocked">← {sl.blocked_by.join(', ')}</div>
        )}
      </div>
      <span
        className="rm-status-chip"
        style={{ color: cs.c, background: cs.bg, borderColor: cs.bd }}
      >
        {sl.status}
      </span>
    </Link>
  );
}

/* ─── Admiral Console ─────────────────────────────────────────────── */

interface ToastState {
  msg: string;
  err: boolean;
}

function AdmiralConsole({
  app,
  onAfter,
}: {
  app: AppStateBundle;
  onAfter: () => void;
}) {
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);
  const [ticking, setTicking] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [showReplan, setShowReplan] = useState(false);

  const showMsg = useCallback((msg: string, err = false) => {
    setToast({ msg, err });
    setTimeout(() => setToast(null), 3000);
  }, []);

  async function send() {
    if (!note.trim()) return;
    setSending(true);
    try {
      const r = await fetch(`/api/captain/apps/${encodeURIComponent(app.app_id)}/note`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ body: note, expects_response: true }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setNote('');
      showMsg('note delivered');
      onAfter();
    } catch (e) {
      showMsg(`note failed: ${e}`, true);
    } finally {
      setSending(false);
    }
  }

  async function tick() {
    if (!app.repo_path) {
      showMsg('no repo_path registered for this app', true);
      return;
    }
    setTicking(true);
    try {
      const r = await fetch(`/api/captain/apps/${encodeURIComponent(app.app_id)}/tick`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ repo_path: app.repo_path }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showMsg('tick dispatched');
      onAfter();
    } catch (e) {
      showMsg(`tick failed: ${e}`, true);
    } finally {
      setTicking(false);
    }
  }

  return (
    <>
      {showReplan && (
        <ReplanDialog
          appId={app.app_id}
          defaultRepoPath={app.repo_path ?? ''}
          onClose={() => setShowReplan(false)}
          onDone={(msg, err) => {
            showMsg(msg, err);
            onAfter();
          }}
        />
      )}

      <div className="console-note-wrap">
        <div className="console-note-lbl">Admiral Note</div>
        <textarea
          className="note-ta"
          placeholder="Write a directive for the captain…"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send();
          }}
        />
        <div className="note-ta-footer">
          <span className="shortcut">⌘↵ to send</span>
          <button
            className="cap-btn cap-btn-primary"
            onClick={send}
            disabled={sending || !note.trim()}
            style={{ padding: '4px 12px' }}
          >
            {sending ? <span className="spin">↻</span> : 'Send'}
          </button>
        </div>
      </div>

      {toast && <div className={`toast ${toast.err ? 'toast-err' : 'toast-ok'}`}>{toast.msg}</div>}

      <div className="sec-lbl">Actions</div>
      <div className="action-row">
        <button className="cap-btn cap-btn-ghost" onClick={() => setShowReplan(true)}>
          Replan
        </button>
        <button className="cap-btn cap-btn-ghost" onClick={tick} disabled={ticking}>
          {ticking ? (
            <>
              <span className="spin">↻</span> ticking…
            </>
          ) : (
            'Tick now'
          )}
        </button>
      </div>

      {app.unread_admiral_notes.length > 0 && (
        <>
          <div className="sec-lbl">Unread Notes ({app.unread_admiral_notes.length})</div>
          {app.unread_admiral_notes.map((n) => (
            <div
              key={n}
              style={{
                fontSize: '10px',
                color: 'var(--yellow)',
                background: 'var(--yellow-d)',
                border: '1px solid var(--yellow-bd)',
                borderRadius: '2px',
                padding: '6px 10px',
              }}
            >
              {n}
            </div>
          ))}
        </>
      )}

      <div className="sec-lbl">Scorecard</div>
      <ScorecardPane sc={app.scorecard} />
    </>
  );
}

function ReplanDialog({
  appId,
  defaultRepoPath,
  onClose,
  onDone,
}: {
  appId: string;
  defaultRepoPath: string;
  onClose: () => void;
  onDone: (msg: string, err?: boolean) => void;
}) {
  const [trigger, setTrigger] = useState('manual');
  const [repo, setRepo] = useState(defaultRepoPath);
  const [refresh, setRefresh] = useState(false);
  const [noLlm, setNoLlm] = useState(false);
  const [loading, setLoading] = useState(false);

  async function go() {
    if (!repo.trim()) {
      onDone('repo_path required', true);
      return;
    }
    setLoading(true);
    try {
      const r = await fetch(`/api/captain/apps/${encodeURIComponent(appId)}/replan`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          trigger,
          repo_path: repo,
          refresh_research: refresh,
          no_llm: noLlm,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      onDone('replan queued');
      onClose();
    } catch (e) {
      onDone(`replan failed: ${e}`, true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog">
        <div className="dialog-title">Force Replan — {appId}</div>
        <div className="dlg-field">
          <label className="dlg-lbl">Trigger</label>
          <input
            className="dlg-input"
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
          />
        </div>
        <div className="dlg-field">
          <label className="dlg-lbl">Repo path</label>
          <input
            className="dlg-input"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          />
        </div>
        <label className="dlg-toggle">
          <input
            type="checkbox"
            checked={refresh}
            onChange={(e) => setRefresh(e.target.checked)}
          />
          <span>refresh_research</span>
        </label>
        <label className="dlg-toggle">
          <input
            type="checkbox"
            checked={noLlm}
            onChange={(e) => setNoLlm(e.target.checked)}
          />
          <span>no_llm (deterministic)</span>
        </label>
        <div className="dlg-btns">
          <button className="cap-btn cap-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="cap-btn cap-btn-primary" onClick={go} disabled={loading}>
            {loading ? <span className="spin">↻</span> : 'Replan'}
          </button>
        </div>
      </div>
    </div>
  );
}

function ScorecardPane({ sc }: { sc: AppStateBundle['scorecard'] }) {
  if (!sc) {
    return (
      <div style={{ color: 'var(--t3)', fontSize: '11px' }}>
        No scorecard. Repo path may not be registered.
      </div>
    );
  }
  const baseline = sc.dimensions.filter((d) => BASELINE_DIMENSIONS.has(d.name));
  const extra = sc.dimensions.filter((d) => !BASELINE_DIMENSIONS.has(d.name));
  return (
    <>
      <div className="sc-agg-row">
        <div className="sc-agg-num" style={{ color: scoreColor(sc.aggregate) }}>
          {sc.aggregate.toFixed(2)}
        </div>
        <div style={{ flex: 1 }}>
          <div className="sc-bar" style={{ height: '4px', marginBottom: '4px' }}>
            <div
              className="sc-fill"
              style={{
                width: `${sc.aggregate * 100}%`,
                background: scoreColor(sc.aggregate),
              }}
            ></div>
          </div>
          <div style={{ fontSize: '9px', color: 'var(--t3)' }}>aggregate score</div>
        </div>
      </div>
      {baseline.length > 0 && (
        <>
          <div className="sec-lbl">baseline dimensions</div>
          {baseline.map((d) => (
            <ScRow key={d.name} d={d} />
          ))}
        </>
      )}
      {extra.length > 0 && (
        <>
          <div className="sec-lbl" style={{ marginTop: '10px' }}>
            app-specific
          </div>
          {extra.map((d) => (
            <ScRow key={d.name} d={d} />
          ))}
        </>
      )}
    </>
  );
}

function ScRow({ d }: { d: { name: string; score: number; rationale: string } }) {
  const [exp, setExp] = useState(false);
  return (
    <div className="sc-row" onClick={() => setExp((e) => !e)}>
      <div className="sc-name-row">
        <span className="sc-name">{d.name}</span>
        <span className="sc-score-val" style={{ color: scoreColor(d.score) }}>
          {d.score.toFixed(2)}
        </span>
      </div>
      <div className="sc-bar">
        <div
          className="sc-fill"
          style={{ width: `${d.score * 100}%`, background: scoreColor(d.score) }}
        ></div>
      </div>
      {exp && <div className="sc-rat">{d.rationale}</div>}
    </div>
  );
}

/* ─── PR status banner ────────────────────────────────────────────── */

type PrStatusKind =
  | 'pr_open'
  | 'pr_merged'
  | 'post_merge'
  | 'roadmap_complete'
  | 'circuit_breaker'
  | 'main_broken'
  | 'stalled';

interface PrStatus {
  kind: PrStatusKind;
  url?: string;
  ts: string;
  rationale: string;
}

/**
 * Walk the captain log tail (newest-first) and surface the most recent
 * lifecycle event for the admiral. Critical states (main_broken, stalled,
 * circuit_breaker) take priority over routine PR lifecycle so a broken
 * main doesn't get hidden by a stale "PR open" header.
 *
 * Priority (most recent wins within band, critical bands beat routine):
 *   CRITICAL: main_broken > circuit_breaker > stalled
 *   ROUTINE: post_merge > pr_merged > pr_open > roadmap_complete
 */
function derivePrStatus(log: CaptainLogEntry[]): PrStatus | null {
  let critical: PrStatus | null = null;
  let routine: PrStatus | null = null;

  for (const e of log) {
    const refs = (e.references as Record<string, string> | null) || {};

    // CRITICAL band — first hit wins (newest first)
    if (!critical) {
      if (e.kind === 'escalation_raised'
          && refs.event === 'post_merge_verify_failed') {
        critical = {
          kind: 'main_broken', ts: e.ts, rationale: e.rationale,
          url: refs.pr_url,
        };
      } else if (e.kind === 'escalation_raised'
                 && refs.event === 'circuit_breaker_tripped') {
        critical = { kind: 'circuit_breaker', ts: e.ts, rationale: e.rationale };
      } else if (e.kind === 'stall_detected') {
        critical = { kind: 'stalled', ts: e.ts, rationale: e.rationale };
      }
    }

    // ROUTINE band — first hit wins
    if (!routine) {
      if (e.kind === 'post_merge_cycle') {
        routine = { kind: 'post_merge', ts: e.ts, rationale: e.rationale };
      } else if (e.kind === 'pull_request_merged') {
        routine = {
          kind: 'pr_merged', ts: e.ts, rationale: e.rationale,
          url: refs.pr_url,
        };
      } else if (e.kind === 'pull_request_opened') {
        routine = {
          kind: 'pr_open', ts: e.ts, rationale: e.rationale,
          url: refs.pr_url,
        };
      } else if (e.kind === 'roadmap_complete') {
        routine = { kind: 'roadmap_complete', ts: e.ts, rationale: e.rationale };
      }
    }

    if (critical && routine) break;  // both bands resolved
  }
  return critical ?? routine;
}

function PrStatusBanner({ status }: { status: PrStatus }) {
  const meta: Record<PrStatusKind, { label: string; color: string; icon: string }> = {
    roadmap_complete: { label: 'Roadmap complete — awaiting PR', color: 'var(--yellow)', icon: '◌' },
    pr_open: { label: 'PR open — ready for review', color: 'var(--green)', icon: '↗' },
    pr_merged: { label: 'PR merged — finalizing cycle', color: 'var(--green)', icon: '✓' },
    post_merge: { label: 'Post-merge — replanning', color: 'var(--accent)', icon: '↻' },
    main_broken: { label: 'CRITICAL — post-merge verify failed (main is broken)', color: 'var(--red)', icon: '⚠' },
    circuit_breaker: { label: 'Paused — circuit breaker tripped', color: 'var(--red)', icon: '⏸' },
    stalled: { label: 'Slice stalled — watchdog killed', color: 'var(--yellow)', icon: '⏱' },
  };
  const m = meta[status.kind];
  return (
    <div
      className="pr-status-banner"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 12px',
        margin: '6px 0',
        border: `1px solid ${m.color}`,
        borderRadius: 4,
        fontSize: 12,
        background: 'var(--bg-elev)',
      }}
    >
      <span style={{ color: m.color, fontWeight: 600 }}>{m.icon} {m.label}</span>
      {status.url && (
        <a
          href={status.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: 'var(--accent)', textDecoration: 'underline' }}
        >
          open PR ↗
        </a>
      )}
      <span style={{ color: 'var(--t3)', marginLeft: 'auto' }}>
        {fmtAgo(status.ts)}
      </span>
    </div>
  );
}
