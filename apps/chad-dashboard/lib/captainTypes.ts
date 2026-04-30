// TypeScript mirror of the Pydantic shapes in
// apps/chad-captain/src/chad_captain/{protocol,scorecard,research}.py.
// Keep in lockstep with the captain API contract.

export type CaptainVerdict =
  | 'accept'
  | 'soft_accept'
  | 'reject_retry'
  | 'reject_hard'
  | 'revert'
  | 'kill_replan'
  | 'escalate';

export type SliceStatus =
  | 'queued'
  | 'in_flight'
  | 'done'
  | 'skipped'
  | 'blocked';

export type CaptainLogKind =
  | 'validate'
  | 'replan'
  | 'dispatch'
  | 'stall_detected'
  | 'note_received'
  | 'note_response'
  | 'escalation_raised'
  | 'escalation_resolved';

export type ProgressKind =
  | 'slice_started'
  | 'tool_call'
  | 'tool_result'
  | 'stdout_chunk'
  | 'heartbeat'
  | 'slice_completing'
  | 'slice_aborted';

export type AppMode = 'autonomous' | 'observe_only';

export interface CurrentSlice {
  slice_id: string;
  app_id: string;
  objective: string;
  system_prompt: string;
  user_prompt: string;
  repo_path: string;
  max_turns: number;
  max_tool_repetitions: number;
  timeout_seconds: number;
  started_at: string | null;
  deadline: string | null;
  issued_at: string;
  expected_rubric_categories: string[];
  parent_slice_id: string | null;
}

export interface RoadmapSlice {
  slice_id: string;
  objective: string;
  phase: string;
  estimated_minutes: number;
  blocked_by: string[];
  status: SliceStatus;
  notes: string;
}

export interface Roadmap {
  app_id: string;
  generated_at: string;
  generated_by: 'initial' | 'replanner' | 'manual';
  objective_summary: string;
  slices: RoadmapSlice[];
}

export interface CaptainLogEntry {
  ts: string;
  app_id: string;
  slice_id: string | null;
  kind: CaptainLogKind;
  verdict: CaptainVerdict | null;
  rubric_delta_pp: number | null;
  rationale: string;
  references: Record<string, unknown>;
}

export interface ProgressEvent {
  ts: string;
  slice_id: string;
  kind: ProgressKind;
  detail: Record<string, unknown>;
}

export interface DimensionScore {
  name: string;
  score: number;
  rationale: string;
  detail: Record<string, unknown>;
}

export interface Scorecard {
  repo_path: string;
  dimensions: DimensionScore[];
  aggregate: number;
}

export interface AppStateBundle {
  app_id: string;
  name: string;
  mode: AppMode;
  repo_path: string | null;
  current_slice: CurrentSlice | null;
  roadmap: Roadmap | null;
  captain_log_tail: CaptainLogEntry[];
  progress_tail: ProgressEvent[];
  unread_admiral_notes: string[];
  scorecard: Scorecard | null;
  error?: string;
}

export interface FleetBundle {
  generated_at: string;
  count: number;
  apps: AppStateBundle[];
  error?: string;
}

export interface AppListEntry {
  app_id: string;
  name: string;
  mode: AppMode;
  repo_path: string | null;
  schedule_hour: number | null;
}

export interface AppsListResponse {
  count: number;
  apps: AppListEntry[];
  error?: string;
}

// Set of dimension names known to be the seven baseline scorecard dims.
// Anything else from a Scorecard.dimensions list is treated as app-specific.
export const BASELINE_DIMENSIONS: ReadonlySet<string> = new Set([
  'tests_present',
  'tests_recent',
  'todo_pressure',
  'skip_pressure',
  'secret_hygiene',
  'file_size_health',
  'docs_present',
]);
