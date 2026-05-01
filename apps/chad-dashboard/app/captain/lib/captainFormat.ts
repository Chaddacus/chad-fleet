// Format/string helpers for the captain dashboard.

import type {
  AppStateBundle,
  CaptainVerdict,
  RoadmapSlice,
} from '@/lib/captainTypes';

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${(s / 3600).toFixed(1)}h ago`;
}

export function elapsed(iso: string | null | undefined): string {
  if (!iso) return '—';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
}

export function trunc(s: string | undefined | null, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

/**
 * Human-readable slice headline. Prefers the LLM-supplied `title` (already
 * sanitized server-side: ≤80 chars, no file paths, no FEATURE: prefix). Falls
 * back to the first sentence of the verbose objective, truncated to `max`.
 */
export function sliceHeadline(
  s: { title?: string | null; objective?: string | null } | null | undefined,
  max = 80,
): string {
  if (!s) return '';
  const title = (s.title ?? '').trim();
  if (title) return trunc(title, max);
  const obj = (s.objective ?? '').trim();
  if (!obj) return '';
  // First sentence wins; otherwise just truncate.
  const firstSentence = obj.split(/(?<=\.)\s+/)[0] ?? obj;
  return trunc(firstSentence.replace(/^\s*(FEATURE|REMEDIATION|HOUSEKEEPING|FIX|TEST|REFACTOR|CHORE|DOCS)\s*:\s*/i, ''), max);
}

export function fmtDetail(d: Record<string, unknown> | undefined | null): string {
  if (!d) return '';
  const entries = Object.entries(d);
  if (!entries.length) return '';
  if (entries.length <= 2) {
    return entries.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(' ');
  }
  return JSON.stringify(d, null, 2);
}

export function scoreColor(v: number): string {
  if (v >= 0.8) return 'var(--green)';
  if (v >= 0.55) return 'var(--yellow)';
  return 'var(--red)';
}

export function verdictLabel(v: CaptainVerdict | null | undefined): string {
  if (!v) return '—';
  return v.replace(/_/g, ' ');
}

export function verdictCls(v: CaptainVerdict | null | undefined): string {
  return v ? `vc-${v}` : '';
}

export function appActivityClass(app: AppStateBundle): 'active' | 'idle' | 'escalating' | 'warn' | 'paused' | 'saturated' {
  const lastValidate = app.captain_log_tail.find((e) => e.kind === 'validate');
  if (lastValidate?.verdict === 'escalate') return 'escalating';
  if (app.paused_until && new Date(app.paused_until).getTime() > Date.now()) {
    return app.pause_reason === 'backlog_saturated' ? 'saturated' : 'paused';
  }
  if (app.current_slice) return 'active';
  if (lastValidate?.verdict === 'reject_retry') return 'warn';
  return 'idle';
}

export function lastValidate(app: AppStateBundle) {
  return app.captain_log_tail.find((e) => e.kind === 'validate');
}

export function roadmapProgress(slices: RoadmapSlice[] | undefined): {
  done: number;
  total: number;
  pct: number;
} {
  if (!slices?.length) return { done: 0, total: 0, pct: 0 };
  const done = slices.filter((s) => s.status === 'done').length;
  return { done, total: slices.length, pct: (done / slices.length) * 100 };
}
