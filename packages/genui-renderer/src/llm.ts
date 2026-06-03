/**
 * High-level view generation pipeline.
 *
 * - Builds system prompt (DESIGN.md + JSON DSL contract).
 * - Calls claudeComplete (subprocess wrapper).
 * - Parses + validates JSON against ViewSpecSchema.
 * - Retries once on validation failure.
 * - Emits stream events.
 */

import { ViewSpecSchema, type ViewSpec, type ViewNode } from './schema.js';
import { buildSystemPrompt, buildRetryPrompt } from './prompt.js';
import { getProvider } from './providers/index.js';

// Re-export subprocess primitives for callers that want them directly.
export { claudeComplete, CLAUDE_BIN, LLMError } from './subprocess.js';
export type { ClaudeCompleteOptions } from './subprocess.js';
export { getProvider } from './providers/index.js';
export type { LLMProvider } from './providers/index.js';

// ---------------------------------------------------------------------------
// Stream events
// ---------------------------------------------------------------------------

export interface PartialEvent {
  type: 'partial';
  text: string;
}

export interface FinalEvent {
  type: 'final';
  view: ViewNode[];
  narrative?: string;
}

export interface ErrorEvent {
  type: 'error';
  message: string;
}

export type StreamEvent = PartialEvent | FinalEvent | ErrorEvent;

export type EventCallback = (event: StreamEvent) => void;

export interface LLMOptions {
  /** Model slug passed to claude -p. Defaults to "sonnet". */
  model?: string;
  /** Disable the design-language section of the system prompt (used in tests). */
  skipDesign?: boolean;
}

// ---------------------------------------------------------------------------
// generateView
// ---------------------------------------------------------------------------

/**
 * Generate a ViewSpec from user state + a request string.
 */
export async function generateView(
  state: object,
  request: string,
  onEvent: EventCallback,
  options: LLMOptions = {},
): Promise<void> {
  const system = buildSystemPrompt(options.skipDesign !== true);
  const userMsg = buildUserMessage(state, request);

  const attempt = async (
    prompt: string,
  ): Promise<{ view: ViewSpec | null; raw: string; error?: string }> => {
    let raw: string;
    try {
      raw = await getProvider().complete(prompt, {
        ...(options.model !== undefined ? { model: options.model } : {}),
        system,
      });
    } catch (err) {
      return { view: null, raw: '', error: String(err) };
    }

    onEvent({ type: 'partial', text: raw });

    let parsed: unknown;
    try {
      parsed = JSON.parse(stripCodeFences(raw));
    } catch (err) {
      return { view: null, raw, error: `JSON parse failed: ${String(err)}` };
    }

    const result = ViewSpecSchema.safeParse(parsed);
    if (!result.success) {
      return { view: null, raw, error: `Schema validation failed: ${result.error.message}` };
    }
    return { view: result.data, raw };
  };

  const first = await attempt(userMsg);
  if (first.view) {
    onEvent(buildFinal(first.view));
    return;
  }

  const retry = await attempt(buildRetryPrompt(userMsg, first.error ?? 'unknown'));
  if (retry.view) {
    onEvent(buildFinal(retry.view));
    return;
  }

  onEvent({
    type: 'error',
    message: retry.error ?? first.error ?? 'generation failed',
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildUserMessage(state: object, request: string): string {
  const stateJson = JSON.stringify(state, null, 2);
  return [
    'User state:',
    '```json',
    stateJson,
    '```',
    '',
    'User request:',
    request,
  ].join('\n');
}

function buildFinal(spec: ViewSpec): FinalEvent {
  const event: FinalEvent = { type: 'final', view: spec.view };
  if (spec.narrative !== undefined) {
    event.narrative = spec.narrative;
  }
  return event;
}

function stripCodeFences(text: string): string {
  const trimmed = text.trim();
  const fenced = trimmed.match(/^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/);
  if (fenced && fenced[1]) return fenced[1].trim();
  return trimmed;
}
