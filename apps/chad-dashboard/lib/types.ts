// Types mirroring state-aggregator's Python models (FleetState, AppSnapshot, InboxItem)

export interface ObsessiveLoopRun {
  run_id: string;
  branch?: string;
  status?: string;
  weighted_avg?: number;
  [key: string]: unknown;
}

export interface AppSnapshot {
  id: string;
  name: string;
  state: string;
  mode: string;
  cadence: string;
  owner_brand: string;
  last_progress_at: string; // ISO datetime string
  blocked_reason?: string | null;
  obsessive_loop_runs: ObsessiveLoopRun[];
  baseline?: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
}

export type Severity = 'info' | 'warn' | 'critical';

export interface InboxItem {
  ts: string; // ISO datetime string
  channel: string;
  severity: Severity;
  title: string;
  body: string;
}

export interface FleetState {
  generated_at: string; // ISO datetime string
  apps: AppSnapshot[];
  inbox_recent: InboxItem[];
  summary: Record<string, unknown>;
}

export interface FleetStateResponse extends FleetState {
  error?: string;
}

export interface InboxResponse {
  items: InboxItem[];
  error?: string;
}

// Mirrors view-registry's SavedView pydantic model.
export interface SavedView {
  id: string;
  name: string;
  description: string;
  prompt: string;
  app_scope: string[];
  pinned: boolean;
  tags: string[];
  created_at: string;
  updated_at: string;
  last_rendered_at?: string | null;
  last_render_html?: string | null;
  last_render_tsx?: string | null;
}

export interface SavedViewListResponse {
  items: SavedView[];
  error?: string;
}

export interface SavedViewResponse {
  view: SavedView | null;
  error?: string;
}
