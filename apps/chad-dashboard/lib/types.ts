// Snapshot contract types — GENERATED from the pydantic source of truth and re-exported
// here so the dashboard has a single import surface. Do not hand-duplicate these; edit the
// pydantic models in state_aggregator.types and run `npm run codegen` in packages/hub-contracts.
export type {
  AppSnapshot,
  FleetState,
  InboxItem,
  SessionSnapshot,
  ToolSnapshot,
  EmailMessage,
  CalendarEvent,
} from '../../../packages/hub-contracts/ts/snapshot';

// Admiral chat contract (hand-authored OpenAI subset — see hub-contracts/schema/admiral-chat).
export type {
  ChatMessage,
  ChatRequest,
  ChatCompletionChunk,
} from '../../../packages/hub-contracts/ts/admiral-chat';

import type { FleetState, InboxItem } from '../../../packages/hub-contracts/ts/snapshot';

// --- Dashboard-local view helpers (NOT part of the cross-boundary contract) ---

// Severity is inlined in the contract's InboxItem; named here for component ergonomics.
export type Severity = 'info' | 'warn' | 'critical';

// A richer view of an obsessive-loop run row. The contract types these as opaque dicts
// (`Record<string, unknown>`); this is the dashboard's read-view of the fields it renders.
export interface ObsessiveLoopRun {
  run_id: string;
  branch?: string;
  status?: string;
  weighted_avg?: number;
  [key: string]: unknown;
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
